#!/usr/bin/env python3
"""CON-163: calibrate sim.py curves against Forge game logs.

Forge is ground truth. The goldfish is a model. This script measures the gap
on the three keys forge_log can actually report (MEASURABLE), then the delta
table is the input to a math fix — not a dashboard.

    # needs Java + DECKWRIGHT_FORGE (see issue setup notes)
    python scripts/con163_calibrate_forge.py --games 50
    python scripts/con163_calibrate_forge.py --games 50 --deck-id 5775225
    python scripts/con163_calibrate_forge.py --from-logs /tmp/forge-cal-logs

Calibration, not selection. Win-rate is printed for context and ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from deckwright import forge_log  # noqa: E402
from deckwright import pressure  # noqa: E402
from deckwright import precon_bake  # noqa: E402
from deckwright import sim  # noqa: E402

# Contract-facing thresholds for the three MEASURABLE keys. NOT_MEASURABLE
# keys are never compared — an unknown is not a zero.
CONDITIONS = [
    {"label": "lands_in_play >= 5", "check": {"lands_in_play": 5}},
    {"label": "lands_in_play >= 6", "check": {"lands_in_play": 6}},
    {"label": "life_gained >= 6", "check": {"life_gained": 6}},
    {"label": "life_gained >= 8", "check": {"life_gained": 8}},
    {"label": "life_drained >= 4", "check": {"life_drained": 4}},
    {"label": "life_drained >= 6", "check": {"life_drained": 6}},
]

# Default hero: Blood Rites (Clavileño) — has gain/drain engines in the 99 so
# all three MEASURABLE keys move. Pirate saves score 0 on life and teach nothing.
DEFAULT_DECK_ID = "5775225"
DEFAULT_OPP_ID = "5775210"  # Ahoy Mateys (aggro pressure opponent)
HERO_NAME = "Deckwright Cal"


def _assert_measurable_only(check: dict) -> None:
    for key in check:
        if key not in forge_log.MEASURABLE:
            raise ValueError(
                f"refusing to compare {key!r}: not in forge_log.MEASURABLE "
                f"(NOT_MEASURABLE must be skipped, not zeroed)"
            )


for _c in CONDITIONS:
    _assert_measurable_only(_c["check"])


def load_hero(deck_id: str | None, saved: str | None) -> dict:
    if saved:
        path = pathlib.Path(saved).expanduser()
        data = json.loads(path.read_text())
        cmdr = data["commander"]
        deck = list(data["deck"])
        cmc = 5
        for c in deck:
            if c.get("name") == cmdr:
                cmc = int(c.get("cmc") or 5)
                break
        return {"id": path.stem, "name": cmdr, "commander": cmdr,
                "deck": deck, "cmc": cmc}
    bake = precon_bake.read_bake(deck_id or DEFAULT_DECK_ID)
    if not bake or not bake.get("deck"):
        raise FileNotFoundError(f"baked deck {deck_id} missing")
    cmc = int((bake.get("commander_card") or {}).get("cmc") or 5)
    return {
        "id": bake.get("id") or deck_id,
        "name": pressure.short_precon_name(bake.get("name") or deck_id),
        "commander": bake["commander"],
        "deck": list(bake["deck"]),
        "cmc": cmc,
    }


def hero_timeline(tl: dict[str, Any]) -> list[dict[str, Any]]:
    """Cumulative MEASURABLE state at the end of each of the hero's turns.

    Forge numbers every player's turn (T1 hero, T2 opp, …). sim.py numbers
    only the goldfish's turns. Comparing Forge T6 to sim T6 would silently
    half the clock. We renumber by hero turns.

    life_drained uses forge_log.cumulative's THESIS-aligned meaning (life lost
    by opponents), not life the hero lost to combat.
    """
    hero = tl["hero"]
    if not hero:
        raise ValueError("timeline has no hero")
    rows = forge_log.cumulative(tl, hero)
    out = []
    n = 0
    for turn, row in zip(tl["turns"], rows):
        if turn["player"] != hero:
            continue
        n += 1
        out.append({
            "hero_turn": n,
            "forge_turn": turn["turn"],
            "life_gained": row["life_gained"],
            "life_drained": row["life_drained"],
            "lands_in_play": row["lands_in_play"],
        })
    return out


def hit_turn(rows: list[dict], check: dict) -> int | None:
    """First hero-turn where every key in check is met, or None."""
    for row in rows:
        if all(row.get(k, 0) >= v for k, v in check.items()):
            return row["hero_turn"]
    return None


def forge_curve(hit_turns: list[int | None], max_turn: int = sim.MAX_TURN) -> list[float]:
    """% of games that have hit by each hero-turn — same shape as sim.run()."""
    n = len(hit_turns)
    if n == 0:
        return [0.0] * max_turn
    pts = []
    for t in range(1, max_turn + 1):
        hits = sum(1 for ht in hit_turns if ht is not None and ht <= t)
        pts.append(round(hits / n * 100, 1))
    return pts


def run_full_log_game(
    *,
    java: str,
    forge: str,
    hero_dck: pathlib.Path,
    opp_dck: pathlib.Path,
    timeout_s: int = pressure.GAME_TIMEOUT_S,
) -> str:
    """One Forge game; return the full stdout (pressure.run_one_game drops it)."""
    cmd = pressure.build_forge_cmd(java, forge, hero_dck.name, opp_dck.name)
    cwd = str(pathlib.Path(forge).parent) if forge.endswith(".jar") else str(hero_dck.parent)
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
                pressure._kill_process_group(proc)
                raise TimeoutError(f"Forge game exceeded {timeout_s}s")
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if line:
                chunks.append(line)
        proc.wait(timeout=5)
    except Exception:
        pressure._kill_process_group(proc)
        raise
    return "".join(chunks)


def run_forge_games(
    hero: dict,
    opp_id: str,
    games: int,
    log_dir: pathlib.Path,
) -> list[dict[str, Any]]:
    engine = pressure.detect_engine()
    if not engine.get("available"):
        raise RuntimeError(
            f"Forge unavailable: {engine.get('reason')} — {engine.get('detail')}"
        )
    java, forge = engine["java"], engine["forge"]
    opp = pressure.load_opponent(opp_id)
    dest = pressure.forge_commander_dir()
    dest.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    run_tag = uuid.uuid4().hex[:8]
    hero_path = dest / f"dw-cal-{run_tag}-hero.dck"
    opp_path = dest / f"dw-cal-{run_tag}-opp.dck"
    hero_path.write_text(
        pressure.to_dck(HERO_NAME, hero["commander"], hero["deck"]),
        encoding="utf-8",
    )
    opp_path.write_text(
        pressure.to_dck(opp["name"], opp["commander"], opp["deck"]),
        encoding="utf-8",
    )

    results = []
    try:
        for i in range(games):
            t0 = time.monotonic()
            text = run_full_log_game(
                java=java, forge=forge, hero_dck=hero_path, opp_dck=opp_path,
            )
            path = log_dir / f"game-{i:03d}.log"
            path.write_text(text, encoding="utf-8")
            hero_ai = f"Ai(1)-{HERO_NAME}"
            tl = forge_log.parse(text, hero=hero_ai)
            rows = hero_timeline(tl)
            try:
                outcome = pressure.parse_sim_output(text, hero_name=HERO_NAME)
            except ValueError:
                outcome = {"won": None, "turns": None}
            results.append({
                "log": str(path),
                "rows": rows,
                "outcome": outcome,
                "forge_outcome": tl["outcome"],
                "elapsed_s": round(time.monotonic() - t0, 1),
            })
            won = outcome.get("won")
            flag = "W" if won else ("L" if won is False else "?")
            print(
                f"  [{i+1}/{games}] {flag} hero_turns={len(rows)} "
                f"lands={rows[-1]['lands_in_play'] if rows else 0} "
                f"gain={rows[-1]['life_gained'] if rows else 0} "
                f"drain={rows[-1]['life_drained'] if rows else 0} "
                f"{results[-1]['elapsed_s']}s",
                flush=True,
            )
    finally:
        for p in (hero_path, opp_path):
            try:
                p.unlink()
            except OSError:
                pass
    return results


def load_from_logs(log_dir: pathlib.Path) -> list[dict[str, Any]]:
    results = []
    for path in sorted(log_dir.glob("game-*.log")):
        text = path.read_text(encoding="utf-8", errors="replace")
        # Hero name is in the log as Ai(1)-...
        tl = forge_log.parse(text)
        # Prefer Ai(1)-* as hero for calibration runs.
        hero = tl["hero"]
        for t in tl["turns"]:
            if t["player"] and t["player"].startswith("Ai(1)-"):
                hero = t["player"]
                break
        tl = forge_log.parse(text, hero=hero)
        rows = hero_timeline(tl)
        try:
            outcome = pressure.parse_sim_output(
                text, hero_name=hero.replace("Ai(1)-", "") if hero else HERO_NAME
            )
        except ValueError:
            outcome = {"won": None, "turns": None}
        results.append({
            "log": str(path),
            "rows": rows,
            "outcome": outcome,
            "forge_outcome": tl["outcome"],
        })
    if not results:
        raise FileNotFoundError(f"no game-*.log in {log_dir}")
    return results


def sim_curves(hero: dict, runs: int, seed: int) -> dict[str, list[float]]:
    conds = [
        {"label": c["label"], "note": "", "check": c["check"]}
        for c in CONDITIONS
    ]
    out = sim.run(hero["deck"], hero["cmc"], set(), conds, runs=runs, seed=seed)
    return {c["label"]: c["points"] for c in out["curves"]}


def compare(
    forge_results: list[dict],
    sim_pts: dict[str, list[float]],
    turns: tuple[int, ...] = (6, 10),
) -> list[dict]:
    table = []
    n = len(forge_results)
    for cond in CONDITIONS:
        hits = [hit_turn(r["rows"], cond["check"]) for r in forge_results]
        fcurve = forge_curve(hits)
        scurve = sim_pts[cond["label"]]
        for t in turns:
            if t < 1 or t > sim.MAX_TURN:
                continue
            s = scurve[t - 1]
            f = fcurve[t - 1]
            table.append({
                "condition": cond["label"],
                "turn": t,
                "sim": s,
                "forge": f,
                "delta": round(f - s, 1),  # forge − sim; negative = sim optimistic
                "n": n,
                "forge_hits_by": hits,
            })
    return table


def print_table(table: list[dict]) -> None:
    print()
    print(f"{'condition':<22} {'turn':>4} {'sim':>7} {'forge':>7} {'delta':>7} {'n':>4}")
    print("-" * 56)
    for row in table:
        print(
            f"{row['condition']:<22} T{row['turn']:<3} "
            f"{row['sim']:>6.1f}% {row['forge']:>6.1f}% "
            f"{row['delta']:>+6.1f} {row['n']:>4}"
        )
    print()
    print("delta = forge − sim. Negative means the goldfish is optimistic.")
    print(f"Compared keys: {', '.join(forge_log.MEASURABLE)}")
    print(f"Skipped (NOT_MEASURABLE): {', '.join(forge_log.NOT_MEASURABLE)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=50)
    ap.add_argument("--deck-id", default=DEFAULT_DECK_ID,
                    help="baked precon id for the hero deck")
    ap.add_argument("--saved", default=None,
                    help="path to a saved deck JSON instead of a precon id")
    ap.add_argument("--opp-id", default=DEFAULT_OPP_ID)
    ap.add_argument("--sim-runs", type=int, default=600)
    ap.add_argument("--seed", type=int, default=163)
    ap.add_argument("--log-dir", default="/tmp/deckwright-forge-cal")
    ap.add_argument("--from-logs", default=None,
                    help="skip Forge; recompute table from saved game-*.log")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    hero = load_hero(args.deck_id, args.saved)
    log_dir = pathlib.Path(args.log_dir)
    print(f"hero: {hero['name']} / {hero['commander']}  "
          f"({len(hero['deck'])} cards, cmc={hero['cmc']})")
    print(f"measurable only: {forge_log.MEASURABLE}")

    if args.from_logs:
        print(f"loading logs from {args.from_logs}")
        forge_results = load_from_logs(pathlib.Path(args.from_logs))
    else:
        print(f"running {args.games} Forge games → {log_dir}")
        forge_results = run_forge_games(hero, args.opp_id, args.games, log_dir)

    n = len(forge_results)
    if n < 1:
        print("no games", file=sys.stderr)
        return 1
    wins = sum(1 for r in forge_results if r["outcome"].get("won") is True)
    print(f"\n{n} games parsed. win-rate {wins}/{n} "
          f"(context only — not a calibration target)")

    print(f"sim.run runs={args.sim_runs} seed={args.seed}")
    sim_pts = sim_curves(hero, args.sim_runs, args.seed)
    table = compare(forge_results, sim_pts)
    print_table(table)

    # Shape summary per condition at T6/T10 for the fix writeup.
    print("sim full curves (hero turns 1–10):")
    for label, pts in sim_pts.items():
        print(f"  {label:<22} {pts}")
    print("forge full curves:")
    for cond in CONDITIONS:
        hits = [hit_turn(r["rows"], cond["check"]) for r in forge_results]
        print(f"  {cond['label']:<22} {forge_curve(hits)}")

    payload = {
        "hero": {k: hero[k] for k in ("id", "name", "commander", "cmc")},
        "n": n,
        "wins": wins,
        "sim_runs": args.sim_runs,
        "seed": args.seed,
        "sim_version": sim.SIM_VERSION,
        "measurable": list(forge_log.MEASURABLE),
        "not_measurable": list(forge_log.NOT_MEASURABLE),
        "table": [{k: v for k, v in row.items() if k != "forge_hits_by"}
                  for row in table],
        "sim_curves": sim_pts,
        "forge_curves": {
            c["label"]: forge_curve(
                [hit_turn(r["rows"], c["check"]) for r in forge_results]
            )
            for c in CONDITIONS
        },
    }
    out_path = pathlib.Path(args.json_out) if args.json_out else log_dir / "calibration.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out_path}")
    return 0 if n >= 50 or args.from_logs else 2


if __name__ == "__main__":
    # Ensure Forge env is visible when the issue's macOS notes don't apply.
    if not os.environ.get("DECKWRIGHT_FORGE"):
        candidate = pathlib.Path.home() / (
            "Applications/forge/"
            "forge-gui-desktop-2.0.13-jar-with-dependencies.jar"
        )
        if candidate.is_file():
            os.environ["DECKWRIGHT_FORGE"] = str(candidate)
    raise SystemExit(main())
