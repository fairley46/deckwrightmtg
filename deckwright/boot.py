"""process boot — warm, refresh catalogs, remeasure understanding.

Three tiers (do not collapse them):

1. **Launch (automatic)** — `run.sh` fail-open git pull; this module warms vocab.
   When grammar/commanders KB is stale: refresh Scryfall catalogs (new words /
   types / keywords / sets), re-derive synergy objects, refresh commander
   mechanics, then remeasure oracle coverage + playstyles precision
   (`print_wave_reeval.py`, offline bulk).

2. **Named print wave (semi-automatic)** — `./update.sh --print-wave NAME`
   re-downloads oracle bulk and writes the print-wave re-eval report. Detection only.

3. **Admit (human / farmed PR)** — new `oracle_signals`, playstyle maps, grammar
   expressions, strategy commitments. Never invented on boot. See
   `docs/KNOW-MAGIC-MAINTENANCE.md`.

The UI polls GET /api/boot. Warm KeepAlive → ready immediately, no splash.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

from deckwright import errors
from deckwright import instance

REPO = Path(__file__).resolve().parents[1]
STALE_DAYS = 14
GRAMMAR = REPO / "data" / "scryfall_grammar.json"
COMMANDERS = REPO / "data" / "commanders.json"
REEVAL_REPORT = REPO / "data" / "print_wave_reeval.json"


@dataclass
class BootStatus:
    ready: bool = False
    phase: str = "starting"
    # starting | warming | refreshing | understanding | ready | degraded
    detail: str = "Starting…"
    refreshed: bool = False
    reevaluated: bool = False
    stale: bool = False
    error: str | None = None
    understanding: dict[str, Any] | None = None
    # live Scryfall preflight. None until probed. ok:false names a
    # challenge / rate limit / timeout without blocking local pages.
    card_source: dict[str, Any] | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        # corpus age is visible on every boot poll — a stale or
        # missing bulk file must not be silent.
        try:
            from deckwright import corpus as _corpus
            corpus_status = _corpus.get_corpus().status()
        except Exception as e:
            print(f"  [boot] corpus status failed: {type(e).__name__}: {e}")
            corpus_status = {
                "loaded": False,
                "card_count": None,
                "error": f"corpus status unavailable ({type(e).__name__})",
            }
        return {
            "ready": self.ready,
            "phase": self.phase,
            "detail": self.detail,
            "refreshed": self.refreshed,
            "reevaluated": self.reevaluated,
            "stale": self.stale,
            "error": self.error,
            "understanding": self.understanding,
            # Two different questions, both worth answering on boot: can we
            # reach the card source at all, and how fresh is the
            # local corpus we would rather read instead.
            "card_source": self.card_source,
            "corpus": corpus_status,
            "env": instance.env_name(),
            "candidate": instance.candidate(),
            "elapsed_ms": int(
                ((self.finished_at or time.time()) - self.started_at) * 1000
            ),
        }


_status = BootStatus()
_lock = threading.Lock()
_task: asyncio.Task | None = None


def status() -> dict[str, Any]:
    with _lock:
        return _status.as_dict()


def _set(**kwargs: Any) -> None:
    with _lock:
        for k, v in kwargs.items():
            setattr(_status, k, v)


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def data_is_stale(limit_days: int = STALE_DAYS) -> bool:
    """True when grammar or commanders KB is older than limit_days.

    Missing timestamps are stale (unknown ≠ fresh). Missing files are stale.
    Matches the fields `check_data_staleness.py` reports for *committed*
    catalogs. The local Scryfall corpus under ~/.deckwright is separate —
    see `corpus_is_missing()`.
    """
    now = datetime.now(timezone.utc)
    checks: list[tuple[Path, tuple[str, ...]]] = [
        (GRAMMAR, ("fetched_at",)),
        (COMMANDERS, ("mechanics_refresh_at", "researched_at_run")),
    ]
    for path, keys in checks:
        if not path.is_file():
            return True
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return True
        ts = None
        for key in keys:
            ts = _parse_ts(data.get(key))
            if ts is not None:
                break
        if ts is None:
            return True
        age = (now - ts.astimezone(timezone.utc)).days
        if age > limit_days:
            return True
    return False


def corpus_is_missing() -> bool:
    """True when the local bulk file is absent.

    grammar/KB can be fresh while oracle_cards.jsonl was never
    downloaded. That is not "fresh enough" — every lookup then hits live
    Scryfall, which is the exposure that took the app down. Unknown ≠ fine.
    """
    try:
        from deckwright import corpus as _corpus
        return not _corpus.ORACLE_JSONL.is_file()
    except Exception:
        # Could not look → treat as missing so boot refreshes rather than
        # silently staying on the live API.
        return True


def load_understanding_summary(
    path: Path = REEVAL_REPORT,
) -> dict[str, Any]:
    """Summarize the last print-wave report for /api/boot.

    Missing/unreadable report → unknown (not a fake 'all good').
    """
    if not path.is_file():
        return {
            "status": "unknown",
            "detail": "No print-wave report yet — could not look",
            "needs_admit": False,
            "action": (
                "After a set dump: ./update.sh --print-wave <name> "
                "(see docs/KNOW-MAGIC-MAINTENANCE.md)"
            ),
        }
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [boot] print-wave report unreadable: {type(e).__name__}: {e}")
        return {
            "status": "unknown",
            "detail": "Could not read print-wave report",
            "needs_admit": False,
            "action": "Re-run scripts/print_wave_reeval.py",
        }

    overall = data.get("overall") or "unknown"
    cov = data.get("coverage") or {}
    ps = data.get("playstyles") or {}
    next_steps = data.get("next_steps") or []
    top = cov.get("unread_lines_top") or []
    needs_admit = bool(next_steps) or overall not in ("ok", "pass", True)
    # Precision failure is a hard stop on admitting — surface it.
    prec_ok = ps.get("precision_ok")
    if prec_ok is False:
        needs_admit = True

    action = None
    if needs_admit and next_steps:
        action = str(next_steps[0])
    elif needs_admit:
        action = (
            "Understanding moved — name the unread gap, then one small admit PR "
            "(docs/KNOW-MAGIC-MAINTENANCE.md). Do not invent keyword signals."
        )
    elif top:
        action = None  # healthy band; thin unread is expected

    return {
        "status": "ok" if overall in ("ok", "pass", True) else str(overall),
        "wave": data.get("wave"),
        "measured_on": data.get("measured_on") or data.get("measured_at"),
        "signal_or_keyword_pct": cov.get("signal_or_keyword_pct"),
        "unread_pct": cov.get("unread_pct"),
        "playstyles_precision": ps.get("precision_pct") or (
            round(float(ps["precision"]) * 100, 1)
            if isinstance(ps.get("precision"), (int, float)) else None
        ),
        "needs_admit": needs_admit,
        "unread_top": [
            {"count": r.get("count"), "line": r.get("line")}
            for r in top[:3]
            if isinstance(r, dict)
        ],
        "detail": (
            f"Understanding {overall}: "
            f"signal∨keyword {cov.get('signal_or_keyword_pct')}%, "
            f"unread {cov.get('unread_pct')}%"
        ),
        "action": action,
    }


def _should_refresh() -> bool:
    if os.environ.get("DECKWRIGHT_BOOT_REFRESH", "").strip() == "1":
        return True
    if os.environ.get("DECKWRIGHT_SKIP_BOOT_REFRESH", "").strip() == "1":
        return False
    # Cadence step 1 is `python -m corpus`. Missing bulk must run that path
    # even when committed grammar/KB are still inside the freshness window.
    return data_is_stale() or (
        corpus_is_missing() and not instance.corpus_readonly()
    )


def _should_reeval() -> bool:
    """Understanding remeasure rides the refresh path unless skipped."""
    if os.environ.get("DECKWRIGHT_SKIP_BOOT_REEVAL", "").strip() == "1":
        return False
    return True


def _run_cadence_refresh() -> tuple[bool, str]:
    script = REPO / "scripts" / "refresh_data_cadence.sh"
    if not script.is_file():
        return False, "refresh_data_cadence.sh missing"
    try:
        proc = subprocess.run(
            ["bash", str(script), "--kb"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=60 * 20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  [boot] cadence failed: {type(e).__name__}: {e}")
        return False, f"cadence failed ({type(e).__name__})"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tip = tail[-1] if tail else f"exit {proc.returncode}"
        return False, f"cadence exit {proc.returncode}: {tip}"
    return True, "Catalogs and commander mechanics refreshed"


def _run_understanding_reeval() -> tuple[bool, str]:
    """Remeasure coverage + playstyles. Does not admit new signals/strategies."""
    script = REPO / "scripts" / "print_wave_reeval.py"
    if not script.is_file():
        return False, "print_wave_reeval.py missing"
    # Offline bulk: catalogs just refreshed; fetch is for named set dumps only.
    argv = [
        os.environ.get("PYTHON", "python3"),
        str(script),
        "--wave",
        f"boot-reeval-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
    ]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=60 * 45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  [boot] understanding reeval failed: {type(e).__name__}: {e}")
        return False, f"understanding reeval failed ({type(e).__name__})"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tip = tail[-1] if tail else f"exit {proc.returncode}"
        # Soft: app still starts; report stays from last good run or partial write.
        return False, f"understanding reeval exit {proc.returncode}: {tip}"
    return True, "Understanding remeasured"


# Mirrored from app.SCRYFALL_REFUSAL_COPY — boot must not import app.
_CARD_SOURCE_COPY = {
    "challenge": (
        "Scryfall is asking us to verify we're not a robot.",
        "This usually clears on its own — try again in a few minutes.",
    ),
    "rate_limit": (
        "Scryfall is rate-limiting us.",
        "Wait a few seconds and try again — nothing you typed was lost.",
    ),
    "timeout": (
        "Scryfall timed out before answering.",
        "The card database did not respond in time. Try again.",
    ),
    "unreachable": (
        "Scryfall didn't answer.",
        "That's the card database, not your deck. Wait a moment and try again.",
    ),
    "transport": (
        "Scryfall couldn't be reached.",
        "That's the card database, not your deck. Wait a moment and try again.",
    ),
}


async def _run_card_source_probe(
    probe: Callable[[], Awaitable[Any]] | None,
) -> dict[str, Any] | None:
    """one look at the live card source. Failure is a named state."""
    if probe is None:
        return None
    try:
        result = await probe()
    except Exception as e:
        # Probe callables may raise ScryfallError or return a dict. Either way
        # we never invent "ok" — unknown is not ready-for-build. app's probe
        # returns a dict; tests may raise. Avoid importing app from boot.
        errors.log_exception("boot card source probe", e)
        kind = errors.known_kind(getattr(e, "kind", None), _CARD_SOURCE_COPY)
        what, how = _CARD_SOURCE_COPY.get(kind, _CARD_SOURCE_COPY["transport"])
        return {
            "ok": False,
            "kind": kind,
            "status": errors.plain_status(getattr(e, "status", 0)),
            "what": what,
            "how": how,
            "error": "scryfall_refused",
            "detail": f"card source probe failed ({type(e).__name__})",
        }
    if isinstance(result, dict):
        return result
    return {"ok": True, "kind": None, "status": 200}


async def run_boot(
    warm: Callable[[], Awaitable[Any]] | None = None,
    card_source_probe: Callable[[], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Warm caches; when stale, refresh catalogs then remeasure understanding."""
    _set(
        ready=False, phase="warming", detail="Warming up…", error=None,
        refreshed=False, reevaluated=False, understanding=None,
        card_source=None,
        finished_at=None, started_at=time.time(),
    )

    if warm is not None:
        try:
            await warm()
        except Exception as e:
            print(f"  [boot] warm-up failed: {type(e).__name__}: {e}")
            cs = await _run_card_source_probe(card_source_probe)
            _set(
                phase="degraded", detail="Started with a warm-up warning",
                error=f"warm-up failed ({type(e).__name__})", ready=True, finished_at=time.time(),
                understanding=load_understanding_summary(),
                card_source=cs,
            )
            return status()

    stale = data_is_stale()
    _set(stale=stale)

    if _should_refresh():
        _set(
            phase="refreshing",
            detail=(
                "Refreshing words, types, and commander reads…"
                if stale else "Updating card catalogs…"
            ),
        )
        ok, msg = await asyncio.to_thread(_run_cadence_refresh)
        if not ok:
            cs = await _run_card_source_probe(card_source_probe)
            _set(
                phase="degraded", detail="Started — catalog refresh skipped",
                error=msg, ready=True, finished_at=time.time(),
                understanding=load_understanding_summary(),
                card_source=cs,
            )
            return status()
        _set(refreshed=True, stale=data_is_stale())

        if _should_reeval():
            _set(
                phase="understanding",
                detail="Re-checking how cards read and which strategies fit…",
            )
            rok, rmsg = await asyncio.to_thread(_run_understanding_reeval)
            summary = load_understanding_summary()
            cs = await _run_card_source_probe(card_source_probe)
            if rok:
                detail = summary.get("detail") or "Understanding up to date"
                if summary.get("needs_admit") and summary.get("action"):
                    detail = "Ready — understanding found gaps to admit"
                if cs and cs.get("ok") is False and cs.get("what"):
                    detail = cs["what"]
                _set(
                    reevaluated=True, understanding=summary,
                    phase="ready", detail=detail, ready=True,
                    finished_at=time.time(),
                    card_source=cs,
                )
            else:
                _set(
                    reevaluated=False, understanding=summary,
                    phase="degraded",
                    detail="Started — understanding remeasure skipped",
                    error=rmsg, ready=True, finished_at=time.time(),
                    card_source=cs,
                )
            return status()

        cs = await _run_card_source_probe(card_source_probe)
        detail = msg
        if cs and cs.get("ok") is False and cs.get("what"):
            detail = cs["what"]
        _set(
            phase="ready", detail=detail, ready=True, finished_at=time.time(),
            understanding=load_understanding_summary(),
            card_source=cs,
        )
        return status()

    cs = await _run_card_source_probe(card_source_probe)
    detail = "Ready"
    if cs and cs.get("ok") is False and cs.get("what"):
        detail = cs["what"]
    _set(
        phase="ready", detail=detail, ready=True, finished_at=time.time(),
        understanding=load_understanding_summary(),
        card_source=cs,
    )
    return status()


def start_background(
    loop: asyncio.AbstractEventLoop,
    warm: Callable[[], Awaitable[Any]] | None = None,
    card_source_probe: Callable[[], Awaitable[Any]] | None = None,
) -> asyncio.Task:
    global _task
    _task = loop.create_task(
        run_boot(warm=warm, card_source_probe=card_source_probe))
    return _task


def reset_for_tests() -> None:
    """Unit tests only — clear module state between cases."""
    global _task
    with _lock:
        _status.ready = False
        _status.phase = "starting"
        _status.detail = "Starting…"
        _status.refreshed = False
        _status.reevaluated = False
        _status.stale = False
        _status.error = None
        _status.understanding = None
        _status.card_source = None
        _status.started_at = time.time()
        _status.finished_at = None
    if _task and not _task.done():
        _task.cancel()
    _task = None
