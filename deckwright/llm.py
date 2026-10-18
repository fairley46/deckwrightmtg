"""
llm.py — where DeckWright's model calls actually go.

Needs an LLM. Ships with an Anthropic Claude adapter (API key or `claude` CLI).
All model calls go through this module, so another frontier model is one
adapter away. This tree does not ship adapters for other providers.

Two Anthropic paths:

  **cli** (default when `claude` is on PATH) — shells out to the `claude`
  binary. No API key required.

  **api** — the Anthropic Messages API, billed per token against API credits.
  Set `DECKWRIGHT_LLM=api` to force this path.

The CLI path presents the same surface the app was already calling —
`client.messages.create(...)` returning something with `.content[0].text` — so
no call site had to change.

## What the CLI path costs you

Every call pays for the CLI's own system prompt: roughly 30k cached tokens
and about 1.5-2s of startup before any work begins. On a subscription that is
usage, not dollars, but it is real latency.

Tools are disabled explicitly. DeckWright wants a text completion, not an agent
with filesystem access.
"""

import asyncio
import contextlib
import json
import os
import shutil

# A wedged `claude` must not park /api/build forever. Prompt is on argv via
# `-p`; the child must not also inherit a stdin that never EOFs.
DEFAULT_CLI_TIMEOUT_S = 180


def _cli_timeout_s() -> float:
    raw = (os.environ.get("DECKWRIGHT_LLM_TIMEOUT") or "").strip()
    if not raw:
        return float(DEFAULT_CLI_TIMEOUT_S)
    try:
        return max(0.1, float(raw))
    except ValueError:
        return float(DEFAULT_CLI_TIMEOUT_S)


class _Block:
    __slots__ = ("type", "text")

    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Response:
    """Just enough of the Messages API response for the call sites we have."""
    __slots__ = ("content", "stop_reason", "usage")

    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason
        self.usage = None


def _flatten(messages: list[dict]) -> str:
    """Fold a messages array into one prompt.

    `claude -p` takes a single prompt, not a conversation. The interrogator is
    the only caller with real multi-turn history, and it needs that history to
    ask its next question — so the turns are labelled rather than concatenated,
    which keeps who-said-what legible to the model.
    """
    if len(messages) == 1 and messages[0].get("role") == "user":
        c = messages[0].get("content")
        return c if isinstance(c, str) else json.dumps(c)

    out = []
    for m in messages:
        c = m.get("content")
        if not isinstance(c, str):
            c = json.dumps(c)
        out.append(f"{'Player' if m.get('role') == 'user' else 'You'}: {c}")
    out.append("You:")
    return "\n\n".join(out)


class _CLIMessages:
    def __init__(self, binary: str):
        self.binary = binary

    async def create(self, *, model: str, max_tokens: int = 0,
                     messages: list[dict], system: str = "",
                     thinking: dict | None = None, **_ignored) -> _Response:
        # output_config / tools / strict schemas are API-only. The CLI path
        # cannot enforce them; callers that need a structured envelope must
        # ask for it in the prompt and parse with extract_brief_payload.
        cmd = [self.binary, "-p", _flatten(messages),
               "--output-format", "json", "--model", model,
               "--allowed-tools", "",
               "--exclude-dynamic-system-prompt-sections"]
        if system:
            cmd += ["--system-prompt", system]

        # Never inherit stdin. `claude -p` already has the prompt on argv; an
        # open parent stdin (uvicorn TTY, launchd pipe that never EOFs) parks
        # communicate() forever.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timeout = _cli_timeout_s()
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            raise RuntimeError(
                f"claude cli timed out after {timeout:g}s "
                f"(set DECKWRIGHT_LLM_TIMEOUT to raise)")
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude cli exited {proc.returncode}: "
                f"{(err or b'').decode()[:300] or (out or b'').decode()[:300]}")
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            raise RuntimeError(f"claude cli returned non-JSON: {out[:300]!r}")
        if payload.get("is_error"):
            raise RuntimeError(f"claude cli error: {str(payload)[:300]}")
        return _Response(payload.get("result") or "")


class _CLIClient:
    """Drop-in for AsyncAnthropic, backed by the Claude Code CLI."""

    def __init__(self, binary: str):
        self.messages = _CLIMessages(binary)
        self.via = "cli"


def make_client():
    """Pick a backend. Returns (client, description) — the description is
    surfaced at startup so it is never a mystery which adapter is running."""
    want = (os.environ.get("DECKWRIGHT_LLM") or "cli").strip().lower()
    binary = shutil.which("claude")

    if want != "api" and binary:
        return _CLIClient(binary), f"Anthropic Claude via CLI ({binary})"

    from anthropic import AsyncAnthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    client = AsyncAnthropic(api_key=key)
    client.via = "api"
    if want != "api" and not binary:
        return client, "Anthropic Claude via API (no `claude` binary on PATH)"
    return client, "Anthropic Claude via API (DECKWRIGHT_LLM=api)"


def can_call(client) -> bool:
    """True when this client can reach Claude without inventing a meter.

    CLI (`via=cli`) needs no API key — `run.sh` unsets the key on purpose so
    calls stay on the CLI. The API path needs a key. A missing key with no CLI
    is "could not look," not a successful call.
    """
    via = getattr(client, "via", None)
    if via == "cli":
        return True
    if via == "api":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return False
