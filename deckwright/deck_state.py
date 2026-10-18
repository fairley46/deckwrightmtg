"""
deck_state.py — match notes, pending change-sets, versions.

D1: suggestions are a pending change set (MR-style). Staging never mutates
the 99. Approve applies one set and forks version vN → vN+1 with a history
snapshot. Reject drops the set and leaves the deck alone.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from deckwright import mechanics
from deckwright import swap


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- match notes

def list_match_notes(data: dict) -> list[dict]:
    notes = data.get("match_notes")
    return list(notes) if isinstance(notes, list) else []


def add_match_note(data: dict, note: dict) -> dict:
    """Append one match note. Mutates data. Returns the stored note."""
    text = (note.get("text") or "").strip()
    opponent = (note.get("opponent_plan") or "").strip()
    result = (note.get("result") or "").strip()  # win / loss / draw / ""
    tags = note.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip()][:8]
    stored = {
        "id": _new_id(),
        "at": note.get("at") or _now(),
        "opponent_plan": opponent,
        "result": result,
        "text": text,
        "tags": tags,
    }
    notes = list_match_notes(data)
    notes.append(stored)
    data["match_notes"] = notes
    return stored


def delete_match_note(data: dict, note_id: str) -> bool:
    notes = list_match_notes(data)
    keep = [n for n in notes if n.get("id") != note_id]
    if len(keep) == len(notes):
        return False
    data["match_notes"] = keep
    return True


# ---------------------------------------------------------------- pending + versions

def ensure_version_fields(data: dict) -> dict:
    if "version" not in data:
        data["version"] = 1
    if "history" not in data or not isinstance(data.get("history"), list):
        data["history"] = []
    if "pending" not in data or not isinstance(data.get("pending"), list):
        data["pending"] = []
    return data


def list_pending(data: dict) -> list[dict]:
    ensure_version_fields(data)
    return list(data["pending"])


def stage_pending(data: dict, changes: list[dict], *,
                  source: str = "suggest", goal: str | None = None,
                  replace_source_goal: bool = True,
                  set_id: str | None = None) -> list[dict]:
    """Stage cut/add pairs as one change set without touching deck[].

    Each change: {cut, add, reason?, role?, add_card?}. `add_card` is a full
    Scryfall-trimmed card so approve does not need role_pools. Rows from one
    call share `set_id` (MR-style). Replaces existing pending from the same
    source+goal when replace_source_goal is True.
    """
    ensure_version_fields(data)
    set_id = (set_id or "").strip() or _new_id()
    staged = []
    for ch in changes:
        cut = (ch.get("cut") or "").strip()
        add = (ch.get("add") or "").strip()
        add_card = ch.get("add_card") if isinstance(ch.get("add_card"), dict) else None
        if add_card and not add:
            add = (add_card.get("name") or "").strip()
        if not cut or not add:
            continue
        row = {
            "id": _new_id(),
            "set_id": set_id,
            "cut": cut,
            "add": add,
            "reason": (ch.get("reason") or "").strip(),
            "role": ch.get("role") or "",
            "source": source,
            "goal": goal,
            "at": _now(),
        }
        if add_card and add_card.get("name") == add:
            # Persist the verified card so approve does not need role_pools.
            row["add_card"] = add_card
        staged.append(row)
    if replace_source_goal:
        keep = [p for p in data["pending"]
                if not (p.get("source") == source and p.get("goal") == goal)]
        data["pending"] = keep + staged
    else:
        data["pending"] = list(data["pending"]) + staged
    return staged


def clear_pending(data: dict, *, pending_id: str | None = None,
                  set_id: str | None = None, source: str | None = None) -> int:
    """Clear pending rows. Scope: one id, one set_id, one source, or all.

    Reject never touches deck[] or version — only the pending list.
    """
    ensure_version_fields(data)
    before = len(data["pending"])
    pending_id = (pending_id or "").strip() or None
    set_id = (set_id or "").strip() or None
    source = (source or "").strip() or None
    if pending_id:
        data["pending"] = [p for p in data["pending"] if p.get("id") != pending_id]
    elif set_id:
        data["pending"] = [p for p in data["pending"] if p.get("set_id") != set_id]
    elif source:
        data["pending"] = [p for p in data["pending"] if p.get("source") != source]
    else:
        data["pending"] = []
    return before - len(data["pending"])


def _apply_one(deck: list[dict], cut: str, add: str,
               role_pools: dict, ci: str) -> list[dict]:
    """Apply one verified swap; raises swap.SwapError."""
    result = swap.apply_swap(deck, cut, add, role_pools, ci=ci)
    return result["deck"]


def approve_pending(data: dict, *,
                    pending_ids: list[str] | None = None,
                    set_id: str | None = None,
                    source: str | None = None,
                    ci: str = "") -> dict[str, Any]:
    """Apply a pending change set, fork version, snapshot history.

    Scope with pending_ids, set_id, or source (checked in that order).
    Omitting all three applies every pending row as one version bump.
    Requires role_pools when a row has no persisted add_card.
    """
    ensure_version_fields(data)
    pending = list(data["pending"])
    if pending_ids is not None:
        want = set(pending_ids)
        pending = [p for p in pending if p.get("id") in want]
    elif set_id:
        set_id = set_id.strip()
        pending = [p for p in pending if p.get("set_id") == set_id]
    elif source:
        source = source.strip()
        pending = [p for p in pending if p.get("source") == source]
    if not pending:
        return {"ok": False, "error": "nothing to approve"}

    role_pools = data.get("role_pools") or {}

    old_version = int(data.get("version") or 1)
    old_deck = copy.deepcopy(data.get("deck") or [])
    new_deck = list(old_deck)
    applied = []
    for p in pending:
        add_card = p.get("add_card")
        by_card = isinstance(add_card, dict) and add_card.get("name") == p.get("add")
        if not by_card and not role_pools:
            return {"ok": False,
                    "error": "no role_pools — cannot verify adds"}
        # The refusal sentence comes from the check, not from the exception.
        problem = (swap.swap_card_problem(new_deck, p["cut"], add_card, ci=ci)
                   if by_card else
                   swap.swap_problem(new_deck, p["cut"], p["add"], role_pools, ci=ci))
        if problem:
            return {"ok": False, "error": problem, "failed": p}
        try:
            if by_card:
                new_deck = swap.apply_swap_card(new_deck, p["cut"], add_card, ci=ci)["deck"]
            else:
                new_deck = _apply_one(new_deck, p["cut"], p["add"], role_pools, ci)
        except swap.SwapError as e:
            print(f"  [approve] swap refused: {e}")
            return {"ok": False, "error": swap.REFUSED, "failed": p}
        applied.append(p)

    # Snapshot the pre-approve tip into history.
    history = list(data.get("history") or [])
    history.append({
        "version": old_version,
        "at": _now(),
        "deck": old_deck,
        "diff": [{"cut": p["cut"], "add": p["add"], "reason": p.get("reason")}
                 for p in applied],
        "mechanics": data.get("mechanics"),
    })
    # Cap history so saves stay small.
    data["history"] = history[-20:]
    data["deck"] = new_deck
    data["version"] = old_version + 1
    data["parent_version"] = old_version
    # Remove applied ids from pending; leave unrelated rows.
    applied_ids = {p["id"] for p in applied}
    data["pending"] = [p for p in data["pending"] if p.get("id") not in applied_ids]
    mechanics.attach_mechanics(data)
    return {
        "ok": True,
        "version": data["version"],
        "parent_version": old_version,
        "applied": applied,
        "pending_left": len(data["pending"]),
    }


def version_summary(data: dict) -> dict[str, Any]:
    ensure_version_fields(data)
    return {
        "version": data.get("version", 1),
        "parent_version": data.get("parent_version"),
        "history_len": len(data.get("history") or []),
        "pending": list_pending(data),
        "history": [
            {"version": h.get("version"), "at": h.get("at"),
             "diff": h.get("diff") or []}
            for h in (data.get("history") or [])
        ],
    }
