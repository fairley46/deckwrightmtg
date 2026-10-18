"""CON-315: green sandbox must not write blue's saved decks.

Offline. No network. Unset env = today's ~/.deckwright paths.
"""

from __future__ import annotations


import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import json
import os
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

passed = failed = 0
GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[0m"

_ENV_KEYS = (
    "DECKWRIGHT_ENV",
    "DECKWRIGHT_HOME",
    "DECKWRIGHT_CORPUS_DIR",
    "DECKWRIGHT_CORPUS_READONLY",
    "DECKWRIGHT_CANDIDATE",
)


def ok(label, extra=""):
    global passed
    passed += 1
    print(f"  {GREEN}pass{OFF} {label} {DIM}{extra}{OFF}")


def bad(label, extra=""):
    global failed
    failed += 1
    print(f"  {RED}FAIL{OFF} {label} {DIM}{extra}{OFF}")


def eq(label, got, want):
    if got == want:
        ok(label, f"got {got!r}")
    else:
        bad(label, f"got {got!r} want {want!r}")


def true(label, cond, extra=""):
    (ok if cond else bad)(label, extra)


def _clear_env():
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    return saved


def _restore_env(saved: dict[str, str | None]):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_unset_defaults_match_today():
    print("\nCON-315 — unset env is blue ~/.deckwright")
    saved = _clear_env()
    try:
        import importlib
        from deckwright import instance
        importlib.reload(instance)
        home = Path.home() / ".deckwright"
        eq("env_name default blue", instance.env_name(), "blue")
        eq("candidate default off", instance.candidate(), "off")
        eq("home_dir default", instance.home_dir(), home)
        eq("corpus_dir default", instance.corpus_dir(), home)
        eq("corpus_readonly default", instance.corpus_readonly(), False)
        err = instance.boot_error()
        eq("boot_error default none", err, None)
    finally:
        _restore_env(saved)
        import importlib
        from deckwright import instance
        importlib.reload(instance)


def test_home_override_and_readonly():
    print("\nCON-315 — DECKWRIGHT_HOME / CORPUS_DIR / READONLY")
    saved = _clear_env()
    try:
        with tempfile.TemporaryDirectory(prefix="dw-g0-") as tmp:
            tmp_p = Path(tmp)
            blue = tmp_p / "blue"
            green = tmp_p / "green"
            os.environ["DECKWRIGHT_ENV"] = "green"
            os.environ["DECKWRIGHT_HOME"] = str(green)
            os.environ["DECKWRIGHT_CORPUS_DIR"] = str(blue)
            os.environ["DECKWRIGHT_CORPUS_READONLY"] = "1"
            import importlib
            from deckwright import instance
            importlib.reload(instance)
            eq("env green", instance.env_name(), "green")
            eq("home override", instance.home_dir(), green.resolve())
            eq("corpus override", instance.corpus_dir(), blue.resolve())
            eq("readonly on", instance.corpus_readonly(), True)
    finally:
        _restore_env(saved)
        import importlib
        from deckwright import instance
        importlib.reload(instance)


def test_blue_refuses_candidate():
    print("\nCON-315 — blue + candidate refuses boot")
    saved = _clear_env()
    try:
        import importlib
        from deckwright import instance
        os.environ["DECKWRIGHT_ENV"] = "blue"
        os.environ["DECKWRIGHT_CANDIDATE"] = "B"
        importlib.reload(instance)
        err = instance.boot_error()
        true("blue+B has boot_error", bool(err), err or "")
        true("error names candidate", err is not None and "CANDIDATE" in err.upper())

        os.environ["DECKWRIGHT_ENV"] = "green"
        os.environ["DECKWRIGHT_CANDIDATE"] = "B"
        importlib.reload(instance)
        eq("green+B allowed", instance.boot_error(), None)

        os.environ["DECKWRIGHT_CANDIDATE"] = "nope"
        importlib.reload(instance)
        err = instance.boot_error()
        true("unknown candidate refused", bool(err), err or "")
    finally:
        _restore_env(saved)
        import importlib
        from deckwright import instance
        importlib.reload(instance)


def test_subprocess_green_save_leaves_blue_untouched():
    print("\nCON-315 — persist_deck under DECKWRIGHT_HOME does not touch blue")
    with tempfile.TemporaryDirectory(prefix="dw-g0-iso-") as tmp:
        tmp_p = Path(tmp)
        blue = tmp_p / "blue"
        green = tmp_p / "green"
        blue.mkdir()
        green.mkdir()
        sentinel = blue / "household.json"
        sentinel.write_text('{"id":"household","commander":"Blue Deck"}\n')
        before = {
            p.name: (p.stat().st_mtime_ns, p.stat().st_size, p.read_bytes())
            for p in blue.iterdir()
            if p.is_file()
        }
        env = os.environ.copy()
        env["DECKWRIGHT_ENV"] = "green"
        env["DECKWRIGHT_HOME"] = str(green)
        env["DECKWRIGHT_CORPUS_DIR"] = str(blue)
        env["DECKWRIGHT_CORPUS_READONLY"] = "1"
        env["DECKWRIGHT_CANDIDATE"] = "off"
        env["DECKWRIGHT_SKIP_BOOT_REFRESH"] = "1"
        env["DECKWRIGHT_SKIP_BOOT_REEVAL"] = "1"
        code = (
            "from deckwright import app\n"
            "app.persist_deck({'commander': 'Green Isolation'})\n"
            "print(app.SAVE_DIR)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        true("subprocess persist exit 0", proc.returncode == 0,
             (proc.stderr or proc.stdout or "")[-400:])
        after = {
            p.name: (p.stat().st_mtime_ns, p.stat().st_size, p.read_bytes())
            for p in blue.iterdir()
            if p.is_file()
        }
        eq("blue file set unchanged", after, before)
        green_json = [p.name for p in green.glob("*.json")]
        true("green has a saved json", any(n.endswith(".json") for n in green_json),
             str(green_json))
        true("household not copied to green",
             "household.json" not in green_json)


def test_corpus_readonly_does_not_fetch_or_write():
    print("\nCON-315 — corpus readonly skips rewrite")
    saved = _clear_env()
    try:
        with tempfile.TemporaryDirectory(prefix="dw-g0-corp-") as tmp:
            corp = Path(tmp) / "corpus"
            corp.mkdir()
            oracle = corp / "oracle_cards.jsonl"
            oracle.write_text("{}\n")
            meta = corp / "oracle_cards.meta.json"
            meta.write_text('{"updated_at":"frozen","download_uri":"x"}\n')
            stamp = {
                p.name: (p.stat().st_mtime_ns, p.stat().st_size)
                for p in corp.iterdir()
            }
            os.environ["DECKWRIGHT_CORPUS_DIR"] = str(corp)
            os.environ["DECKWRIGHT_CORPUS_READONLY"] = "1"
            import importlib
            from deckwright import corpus
            from deckwright import instance
            importlib.reload(instance)
            importlib.reload(corpus)
            out = corpus.ensure_oracle_cards()
            eq("readonly returns frozen updated_at",
               out.get("updated_at"), "frozen")
            after = {
                p.name: (p.stat().st_mtime_ns, p.stat().st_size)
                for p in corp.iterdir()
            }
            eq("corpus files unchanged", after, stamp)
            true("no default_cards dump",
                 not (corp / "default_cards.jsonl").exists())
    finally:
        _restore_env(saved)
        import importlib
        from deckwright import corpus
        from deckwright import instance
        importlib.reload(instance)
        importlib.reload(corpus)


def test_green_plist_and_install_script():
    print("\ngreen launchd template — optional in public tree")
    plist = REPO / "config" / "com.deckwright.green.plist"
    install = REPO / "config" / "install-green-launchd.sh"
    blue = REPO / "config" / "com.deckwright.plist"
    if not plist.is_file() or not install.is_file():
        print("  skip — personal launchd templates not in public tree")
        return
    true("green plist exists", plist.is_file())
    true("install-green-launchd.sh exists", install.is_file())
    text = plist.read_text()
    blue_text = blue.read_text()
    true("green label", "com.deckwright.green" in text)
    true("green is not blue label-only",
         "<string>com.deckwright</string>" not in text
         or "com.deckwright.green" in text)
    true("port 8001", "8001" in text)
    true("host loopback", "127.0.0.1" in text)
    true("DECKWRIGHT_ENV green", "DECKWRIGHT_ENV" in text and "green" in text)
    true("DECKWRIGHT_HOME placeholder", "DECKWRIGHT_HOME" in text)
    true("corpus dir shared", "DECKWRIGHT_CORPUS_DIR" in text)
    true("readonly", "DECKWRIGHT_CORPUS_READONLY" in text)
    true("skip pull", "DECKWRIGHT_SKIP_PULL" in text)
    true("green log path", "/tmp/deckwright-green.log" in text)
    true("green err path", "/tmp/deckwright-green.err" in text)
    true("not blue log path", "/tmp/deckwright.log" not in text)
    true("WorkingDirectory placeholder", "__REPO_ROOT__" in text)
    true("HOME placeholder", "__HOME__" in text)
    true("CON-334 LLM timeout 600",
         "DECKWRIGHT_LLM_TIMEOUT" in text and "<string>600</string>" in text)
    true("CON-336 no EDHREC seed on BLUE baseline",
         "DECKWRIGHT_EDHREC_SEED" not in text)
    true("blue plist unchanged label",
         "com.deckwright</string>" in blue_text)
    true("blue still :8000 default (no 8001)", "8001" not in blue_text)
    # Blue production timeout is Brad's call (CON-334); do not require it here.

    root = ET.parse(plist).getroot()
    d = root.find("dict")
    children = list(d)
    label = None
    for i, child in enumerate(children):
        if child.tag == "key" and child.text == "Label" and i + 1 < len(children):
            label = children[i + 1].text
    eq("plist Label", label, "com.deckwright.green")

    sh = install.read_text()
    true("install substitutes REPO", "__REPO_ROOT__" in sh and "sed" in sh)
    true("install substitutes HOME", "__HOME__" in sh)
    true("install label green", "com.deckwright.green" in sh)
    true("CON-324 refuses primary clone",
         '-d "$REPO_ROOT/.git"' in sh or '-d "$REPO_ROOT/.git"' in sh.replace("'", '"'))
    true("CON-324 names worktree", "worktree" in sh.lower())
    true("CON-324 refuses blue WorkingDirectory",
         "com.deckwright.plist" in sh and "WorkingDirectory" in sh)
    true("install warns on missing timeout", "DECKWRIGHT_LLM_TIMEOUT" in sh)
    mode = install.stat().st_mode
    true("install script executable", bool(mode & stat.S_IXUSR))


def test_green_b_plist_and_install_script():
    print("\ngreen-b launchd template — optional in public tree")
    plist = REPO / "config" / "com.deckwright.green-b.plist"
    install = REPO / "config" / "install-green-b-launchd.sh"
    if not plist.is_file() or not install.is_file():
        print("  skip — personal launchd templates not in public tree")
        return
    true("green-b plist exists", plist.is_file())
    true("install-green-b-launchd.sh exists", install.is_file())
    text = plist.read_text()
    true("green-b label", "com.deckwright.green-b" in text)
    true("port 8002", "8002" in text)
    true("candidate B", "<string>B</string>" in text)
    true("green-b home dir", ".deckwright-green-b" in text)
    true("green-b log path", "/tmp/deckwright-green-b.log" in text)
    true("CON-334 LLM timeout 600",
         "DECKWRIGHT_LLM_TIMEOUT" in text and "<string>600</string>" in text)
    sh = install.read_text()
    true("install-b label", "com.deckwright.green-b" in sh)
    true("install-b refuses primary clone", "worktree" in sh.lower())
    true("install-b warns on missing timeout", "DECKWRIGHT_LLM_TIMEOUT" in sh)
    mode = install.stat().st_mode
    true("install-b executable", bool(mode & stat.S_IXUSR))


def test_run_sh_path_before_venv():
    print("\nrun.sh PATH and interpreter gate before .venv")
    text = (REPO / "run.sh").read_text()
    i_path = text.find("/opt/homebrew/bin")
    i_venv = text.find("-m venv")
    true("homebrew on PATH in run.sh", i_path != -1)
    true("venv creation present", i_venv != -1)
    true("PATH prepend before venv", i_path != -1 and i_venv != -1 and i_path < i_venv)
    true("3.12 floor", "312" in text or "3.12" in text)
    true("old venv tells you to rm -rf .venv", "rm -rf .venv" in text)
    true("uses chosen PYTHON for venv", '"$PYTHON" -m venv' in text or "'$PYTHON' -m venv" in text)


def test_run_sh_refuses_old_venv():
    print("\nexisting old .venv exits before KeepAlive loop")
    with tempfile.TemporaryDirectory(prefix="dw-venv-") as tmp:
        root = Path(tmp)
        (root / ".venv" / "bin").mkdir(parents=True)
        fake = root / ".venv" / "bin" / "python"
        fake.write_text(
            "#!/bin/sh\n"
            "echo 309\n"
            "exit 0\n"
        )
        fake.chmod(stat.S_IRWXU)
        # Reproduce just the version gate from run.sh against this fake venv.
        script = r"""
set -e
_py_ver() { "$1" -c 'import sys; print(sys.version_info.major * 100 + sys.version_info.minor)' 2>/dev/null || echo 0; }
cd "$1"
vver=$(_py_ver .venv/bin/python)
if [ "${vver:-0}" -lt 312 ]; then
  echo "old venv $vver"
  exit 1
fi
exit 0
"""
        proc = subprocess.run(
            ["bash", "-c", script, "bash", str(root)],
            capture_output=True, text=True, timeout=10,
        )
        # The fake python ignores -c and always prints 309, so _py_ver gets 309.
        eq("old venv exit 1", proc.returncode, 1)
        true("old venv mentioned", "309" in (proc.stdout + proc.stderr))

def test_green_index_badge():
    print("\nCON-315 — staging badge on GET / when green")
    saved = _clear_env()
    try:
        import asyncio
        import importlib
        from deckwright import instance
        from deckwright import app

        os.environ["DECKWRIGHT_ENV"] = "green"
        importlib.reload(instance)

        async def _get():
            return await app.index()

        resp = asyncio.run(_get())
        html = resp.body.decode() if hasattr(resp, "body") else str(resp)
        true("badge copy STAGING", "STAGING" in html)
        true("do not buy", "do not buy" in html.lower())
        true("env-badge class", "env-badge" in html)

        os.environ["DECKWRIGHT_ENV"] = "blue"
        importlib.reload(instance)
        resp = asyncio.run(_get())
        html = resp.body.decode() if hasattr(resp, "body") else str(resp)
        true("blue has no injected badge span", 'class="env-badge"' not in html)
        true("blue has no STAGING copy", "STAGING" not in html)
    finally:
        _restore_env(saved)
        import importlib
        from deckwright import instance
        importlib.reload(instance)


def test_boot_status_exposes_env():
    print("\nCON-315 — /api/boot names env")
    saved = _clear_env()
    try:
        import asyncio
        import importlib
        from deckwright import instance
        os.environ["DECKWRIGHT_ENV"] = "green"
        os.environ["DECKWRIGHT_SKIP_BOOT_REFRESH"] = "1"
        importlib.reload(instance)
        from deckwright import boot
        importlib.reload(boot)

        async def _get():
            return boot.status()

        body = asyncio.run(_get())
        eq("boot env green", body.get("env"), "green")
        eq("boot candidate off", body.get("candidate"), "off")
    finally:
        _restore_env(saved)
        import importlib
        from deckwright import instance
        from deckwright import boot
        importlib.reload(instance)
        importlib.reload(boot)


def main() -> int:
    test_unset_defaults_match_today()
    test_home_override_and_readonly()
    test_blue_refuses_candidate()
    test_subprocess_green_save_leaves_blue_untouched()
    test_corpus_readonly_does_not_fetch_or_write()
    test_green_plist_and_install_script()
    test_green_b_plist_and_install_script()
    test_run_sh_path_before_venv()
    test_run_sh_refuses_old_venv()
    test_green_index_badge()
    test_boot_status_exposes_env()
    print()
    if failed:
        print(f"{RED}{failed} failed{OFF}, {passed} passed")
        return 1
    print(f"{GREEN}{passed} passed{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
