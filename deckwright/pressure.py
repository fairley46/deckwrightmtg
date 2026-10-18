"""
pressure.py — Forge pressure test (CON-114).

Opt-in whole-deck validation against three fixed precon opponents. Separate
from the goldfish: nothing on the build path imports or calls this unless the
player presses the button. Absent Java or Forge, both endpoints return
available:false and the rest of the app is unchanged.
"""

from __future__ import annotations

import hashlib
import os
import sys
import pathlib
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from typing import Any, Callable

from deckwright import precon_bake

# Fixed opponents — one of each archetype so runs are comparable.
# Names are short forms (set suffix stripped). Ids are Archidekt precon ids
# baked under data/precons/.
OPONENTS: list[dict[str, str]] = [
    {"id": "5775210", "name": "Ahoy Mateys", "archetype": "aggro"},
    {"id": "5226423", "name": "Virtue and Valor", "archetype": "midrange"},
    {"id": "2209161", "name": "Eternal Bargain", "archetype": "control"},
]

ARCHETYPE_ORDER = {"aggro": 0, "midrange": 1, "control": 2}

DEFAULT_GAMES = 10
# Forge's own -c clock (seconds) before it draws a stuck game.
FORGE_CLOCK_S = 300
# Hard kill if the JVM itself hangs past the clock.
GAME_TIMEOUT_S = FORGE_CLOCK_S + 60

HERO_DECK_NAME = "Deckwright Hero"

_GAME_RESULT_WIN = re.compile(
    r"Game Result:\s*Game\s+\d+\s+ended in\s+\d+\s+ms\.\s*(.+?)\s+has won!",
    re.IGNORECASE,
)
_GAME_RESULT_DRAW = re.compile(
    r"Game Result:\s*Game\s+\d+\s+ended in a Draw!",
    re.IGNORECASE,
)
_TURN_LINE = re.compile(r"Game Outcome:\s*Turn\s+(\d+)\s*$", re.IGNORECASE)

# Injected for tests. Production uses detect_engine / run_one_game.
_detect_engine_fn: Callable[[], dict] | None = None
_run_one_game_fn: Callable[..., dict] | None = None


def deck_fingerprint(commander: str, deck: list[dict]) -> str:
    names = sorted((c.get("name") or "") for c in deck)
    raw = commander.strip() + "\n" + "\n".join(names)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def short_precon_name(full: str) -> str:
    return full.split(" - ")[0].strip() if full else full


def to_dck(name: str, commander: str, cards: list[dict]) -> str:
    """Forge Commander .dck — metadata, [commander], [main]. Names only."""
    lines = [
        "[metadata]",
        f"Name={name}",
        "Deck Type=Commander",
        "",
        "[commander]",
        f"1 {commander}",
        "",
        "[main]",
    ]
    for card in cards:
        card_name = (card.get("name") or "").strip()
        if not card_name:
            continue
        lines.append(f"1 {card_name}")
    lines.append("")
    return "\n".join(lines)


def parse_sim_output(text: str, hero_name: str = HERO_DECK_NAME) -> dict:
    """Pull winner + turn count from Forge stdout. Nothing else."""
    turns: int | None = None
    won: bool | None = None
    for line in text.splitlines():
        line = line.strip()
        m_turn = _TURN_LINE.match(line)
        if m_turn:
            turns = int(m_turn.group(1))
            continue
        m_win = _GAME_RESULT_WIN.search(line)
        if m_win:
            winner = m_win.group(1).strip()
            # SimulateMatch names players Ai(1)-{deck.Name}, Ai(2)-...
            won = (
                winner.startswith("Ai(1)-")
                or winner == hero_name
                or winner.endswith(hero_name)
            )
            continue
        if _GAME_RESULT_DRAW.search(line):
            won = False
    if won is None:
        raise ValueError("no Game Result line in Forge output")
    return {"won": won, "turns": turns}


def find_java() -> str | None:
    return shutil.which("java")


def find_forge() -> str | None:
    """Path to a Forge jar or launcher binary, or None."""
    env = (os.environ.get("DECKWRIGHT_FORGE")
           or os.environ.get("FORGE_JAR")
           or "").strip()
    if env and pathlib.Path(env).is_file():
        return env

    home = (os.environ.get("FORGE_HOME") or "").strip()
    if home:
        home_path = pathlib.Path(home)
        for candidate in (
            home_path / "forge.jar",
            home_path / "forge-gui-desktop.jar",
        ):
            if candidate.is_file():
                return str(candidate)
        for jar in sorted(home_path.glob("forge*.jar")):
            return str(jar)
        exe = home_path / "forge"
        if exe.is_file() and os.access(exe, os.X_OK):
            return str(exe)

    on_path = shutil.which("forge")
    if on_path:
        return on_path

    search_roots = [
        pathlib.Path.home() / "Forge",
        pathlib.Path.home() / "forge",
        pathlib.Path("/opt/forge"),
        pathlib.Path("/usr/local/forge"),
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        for jar in sorted(root.glob("forge*.jar")):
            return str(jar)
        exe = root / "forge"
        if exe.is_file() and os.access(exe, os.X_OK):
            return str(exe)
    return None


def detect_engine() -> dict:
    if _detect_engine_fn is not None:
        return _detect_engine_fn()
    if not find_java():
        return {
            "available": False,
            "reason": "java_missing",
            "detail": "Java is not installed; Forge needs a JRE to run.",
        }
    forge = find_forge()
    if not forge:
        return {
            "available": False,
            "reason": "forge_missing",
            "detail": "Forge is not installed; set DECKWRIGHT_FORGE to the forge.jar path.",
        }
    return {"available": True, "java": find_java(), "forge": forge}


def forge_data_dir() -> pathlib.Path:
    """Where this platform's Forge keeps its user data.

    macOS puts it under Application Support; ~/.forge is the Linux and Windows
    convention. Writing decks to the wrong one is silent: Forge starts, reports
    "Could not load deck", plays nothing, and the run ends with no Game Result
    line to parse. That is what it did here until this was found by running it.
    """
    if sys.platform == "darwin":
        mac = pathlib.Path.home() / "Library" / "Application Support" / "Forge"
        if mac.is_dir():
            return mac
    return pathlib.Path.home() / ".forge"


def forge_commander_dir() -> pathlib.Path:
    override = (os.environ.get("DECKWRIGHT_FORGE_DECKS") or "").strip()
    if override:
        return pathlib.Path(override)
    return forge_data_dir() / "decks" / "commander"


def load_opponent(precon_id: str) -> dict:
    cached = precon_bake.read_bake(precon_id)
    if not cached or not cached.get("deck") or not cached.get("commander"):
        raise FileNotFoundError(f"baked precon {precon_id} missing")
    return {
        "id": precon_id,
        "name": short_precon_name(cached.get("name") or precon_id),
        "commander": cached["commander"],
        "deck": list(cached["deck"]),
    }


def opponent_schedule(games: int) -> list[dict[str, str]]:
    """Round-robin across OPONENTS so finished games land evenly."""
    if games < 1:
        return []
    return [OPONENTS[i % len(OPONENTS)] for i in range(games)]


def build_forge_cmd(
    java: str,
    forge: str,
    hero_file: str,
    opp_file: str,
) -> list[str]:
    """`java -jar forge.jar sim ...` or bare `forge sim ...`."""
    if forge.endswith(".jar"):
        cmd = [java, "-jar", forge]
    else:
        cmd = [forge]
    # Plain sim loads .dck from Forge's commander dir (see run worker).
    # -n 1 so finished games can be reported as they land.
    return cmd + [
        "sim",
        "-d", hero_file, opp_file,
        "-n", "1",
        "-f", "Commander",
        "-c", str(FORGE_CLOCK_S),
    ]


def run_one_game(
    *,
    java: str,
    forge: str,
    hero_dck: pathlib.Path,
    opp_dck: pathlib.Path,
    hero_name: str = HERO_DECK_NAME,
    timeout_s: int = GAME_TIMEOUT_S,
) -> dict:
    """Run one Forge game; kill the process group on hang."""
    if _run_one_game_fn is not None:
        return _run_one_game_fn(
            java=java, forge=forge, hero_dck=hero_dck, opp_dck=opp_dck,
            hero_name=hero_name, timeout_s=timeout_s,
        )

    cmd = build_forge_cmd(java, forge, hero_dck.name, opp_dck.name)
    # Decks must live in Forge's commander directory for non-tournament sim.
    # Forge resolves res/ — 33,000 card definitions — relative to its working
    # directory, so it has to run from beside its own jar. Running from the deck
    # folder starts the JVM, loads nothing, plays no game, and produces output
    # with no Game Result line in it. The decks are passed by name and read from
    # Forge's own decks directory, so cwd does not need to be theirs.
    forge_jar = find_forge()
    cwd = str(pathlib.Path(forge_jar).parent) if forge_jar and forge_jar.endswith(".jar") \
        else str(hero_dck.parent)
    # Start a new session so we can kill the whole JVM tree on timeout.
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    chunks: list[str] = []
    try:
        assert proc.stdout is not None
        deadline = time.monotonic() + timeout_s
        while True:
            if time.monotonic() > deadline:
                _kill_process_group(proc)
                raise TimeoutError(f"Forge game exceeded {timeout_s}s")
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if line:
                # Keep only lines the parser needs — full game logs are huge.
                if (line.startswith("Game Outcome:")
                        or line.startswith("Game Result:")
                        or line.startswith("Match Result:")):
                    chunks.append(line)
        rc = proc.wait(timeout=5)
        if rc not in (0, None):
            # Still try to parse — Forge sometimes exits non-zero after printing.
            pass
    except Exception:
        _kill_process_group(proc)
        raise
    return parse_sim_output("".join(chunks), hero_name=hero_name)


def _kill_process_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# In-memory run store — one active run per deck fingerprint.
# ---------------------------------------------------------------------------

class PressureStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, dict] = {}
        self._active_by_deck: dict[str, str] = {}

    def get(self, run_id: str) -> dict | None:
        with self._lock:
            run = self._runs.get(run_id)
            return _public_view(run) if run else None

    def start(
        self,
        *,
        commander: str,
        deck: list[dict],
        games: int = DEFAULT_GAMES,
    ) -> dict:
        engine = detect_engine()
        if not engine.get("available"):
            return {
                "available": False,
                "reason": engine["reason"],
                "detail": engine["detail"],
            }

        fp = deck_fingerprint(commander, deck)
        with self._lock:
            existing = self._active_by_deck.get(fp)
            if existing and existing in self._runs:
                run = self._runs[existing]
                if not run.get("finished"):
                    return {"run_id": existing, "available": True}

            run_id = uuid.uuid4().hex
            schedule = opponent_schedule(games)
            opponents = _empty_opponent_slots()
            run = {
                "run_id": run_id,
                "fingerprint": fp,
                "commander": commander,
                "deck": deck,
                "total": games,
                "done": 0,
                "finished": False,
                "record": {"won": 0, "lost": 0},
                "turns": {"min": None, "max": None},
                "opponents": opponents,
                "schedule": schedule,
                "error": None,
                "engine": engine,
            }
            self._runs[run_id] = run
            self._active_by_deck[fp] = run_id

        thread = threading.Thread(
            target=self._worker, args=(run_id,), daemon=True, name=f"pressure-{run_id[:8]}",
        )
        thread.start()
        return {"run_id": run_id, "available": True}

    def _worker(self, run_id: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
        if not run:
            return
        engine = run["engine"]
        java, forge = engine["java"], engine["forge"]
        try:
            with tempfile.TemporaryDirectory(prefix="deckwright-pressure-") as tmp:
                # Prefer Forge's commander dir so `sim -d file.dck` resolves.
                # Fall back to the temp dir + DECKWRIGHT_FORGE_DECKS for tests.
                dest = forge_commander_dir()
                try:
                    dest.mkdir(parents=True, exist_ok=True)
                    work = dest
                except OSError:
                    work = pathlib.Path(tmp)

                hero_path = work / f"dw-{run_id[:8]}-hero.dck"
                hero_path.write_text(
                    to_dck(HERO_DECK_NAME, run["commander"], run["deck"]),
                    encoding="utf-8",
                )

                opp_paths: dict[str, pathlib.Path] = {}
                for opp in OPONENTS:
                    loaded = load_opponent(opp["id"])
                    path = work / f"dw-{run_id[:8]}-opp-{opp['id']}.dck"
                    path.write_text(
                        to_dck(opp["name"], loaded["commander"], loaded["deck"]),
                        encoding="utf-8",
                    )
                    opp_paths[opp["id"]] = path

                for i, opp in enumerate(run["schedule"]):
                    result = run_one_game(
                        java=java,
                        forge=forge,
                        hero_dck=hero_path,
                        opp_dck=opp_paths[opp["id"]],
                    )
                    self._record_game(run_id, opp, result)

                # Clean our .dck files out of the shared Forge dir.
                if work == dest:
                    for p in [hero_path, *opp_paths.values()]:
                        try:
                            p.unlink()
                        except OSError:
                            pass
        except Exception as e:
            with self._lock:
                run = self._runs.get(run_id)
                if run:
                    run["error"] = f"{type(e).__name__}: {e}"
                    run["finished"] = True
        finally:
            with self._lock:
                run = self._runs.get(run_id)
                if run:
                    run["finished"] = True
                    if run.get("fingerprint") and self._active_by_deck.get(run["fingerprint"]) == run_id:
                        # Keep fingerprint mapping so a finished run can be
                        # re-polled; a new start() replaces it.
                        pass

    def _record_game(self, run_id: str, opp: dict, result: dict) -> None:
        with self._lock:
            run = self._runs[run_id]
            won = bool(result.get("won"))
            turns = result.get("turns")
            slot = next(o for o in run["opponents"] if o["name"] == opp["name"])
            game = {"won": won, "turns": turns}
            slot["games"].append(game)
            if won:
                slot["record"]["won"] += 1
                run["record"]["won"] += 1
            else:
                slot["record"]["lost"] += 1
                run["record"]["lost"] += 1
            if isinstance(turns, int):
                if run["turns"]["min"] is None or turns < run["turns"]["min"]:
                    run["turns"]["min"] = turns
                if run["turns"]["max"] is None or turns > run["turns"]["max"]:
                    run["turns"]["max"] = turns
            run["done"] += 1
            if run["done"] >= run["total"]:
                run["finished"] = True


def _opponent_art(precon_id: str) -> str:
    """Art for an opponent's box. Empty if the bake has none — the box still
    renders, it just has no face on it."""
    try:
        cached = precon_bake.read_bake(precon_id) or {}
    except Exception:
        return ""
    return cached.get("art") or (cached.get("commander_card") or {}).get("art") or ""


def _empty_opponent_slots() -> list[dict]:
    slots = [
        {
            "name": o["name"],
            "archetype": o["archetype"],
            # The id and the art so a loss is a door into that deck's page.
            # We hold all 155 with a full analysis; the deck that beat her
            # should be one click from what it does, not a dead end.
            "precon_id": o["id"],
            "art": _opponent_art(o["id"]),
            "record": {"won": 0, "lost": 0},
            "games": [],
        }
        for o in OPONENTS
    ]
    slots.sort(key=lambda o: ARCHETYPE_ORDER.get(o["archetype"], 99))
    return slots


def _public_view(run: dict | None) -> dict | None:
    if not run:
        return None
    turns = run["turns"]
    out: dict[str, Any] = {
        "done": run["done"],
        "total": run["total"],
        "finished": run["finished"],
        "record": dict(run["record"]),
        "turns": {
            "min": turns["min"],
            "max": turns["max"],
        },
        "opponents": [
            {
                "name": o["name"],
                "archetype": o["archetype"],
                # The face and the link. This view rebuilds each opponent from a
                # fixed list rather than copying it, so anything added to the
                # slot and not added here is silently dropped on the way out —
                # which is how the boxes lost their art while the backend had it.
                "precon_id": o.get("precon_id"),
                "art": o.get("art"),
                "expected": o.get("expected"),
                "record": dict(o["record"]),
                "games": list(o["games"]),
            }
            for o in run["opponents"]
        ],
        "available": True,
    }
    if run.get("error"):
        out["error"] = run["error"]
    return out


STORE = PressureStore()
