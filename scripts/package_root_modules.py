#!/usr/bin/env python3
"""move root app modules into deckwright/ and rewrite imports.

Idempotent enough to re-run after a partial apply: skips git mv when the
destination already exists. Does not touch data/ or ~/.deckwright paths.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "deckwright"

MODULES = sorted(
    p.stem
    for p in ROOT.glob("*.py")
    if p.name != "package_root_modules.py"
)
# After a move, modules live under PKG.
if not MODULES:
    MODULES = sorted(p.stem for p in PKG.glob("*.py") if p.name != "__init__.py" and p.name != "__main__.py")

MOD_SET = set(MODULES)

# Stdlib / third-party names that must never be rewritten as package imports.
# Anything not in MOD_SET is left alone anyway; this is documentation.
assert "os" not in MOD_SET and "fastapi" not in MOD_SET

REPO_PATH_FILES = {
    "boot.py",
    "bug_report.py",
    "commanders.py",
    "mechanics.py",
    "playstyles.py",
    "precon_bake.py",
    "release_calendar.py",
    "resolve.py",
    "synergy.py",
    "app.py",
}


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)


def git_mv_modules() -> None:
    PKG.mkdir(exist_ok=True)
    for name in MODULES:
        src = ROOT / f"{name}.py"
        dst = PKG / f"{name}.py"
        if dst.exists() and not src.exists():
            continue
        if not src.exists():
            raise SystemExit(f"missing source {src}")
        run(["git", "mv", str(src), str(dst)])


def write_package_scaffold() -> None:
    (PKG / "__init__.py").write_text(
        '"""Deckwright application package."""\n',
        encoding="utf-8",
    )
    # Delegate to app.py's existing __main__ block — zero behavior drift.
    (PKG / "__main__.py").write_text(
        '"""python -m deckwright — same entry as the old root app.py."""\n'
        "import runpy\n\n"
        'runpy.run_module("deckwright.app", run_name="__main__")\n',
        encoding="utf-8",
    )


def rewrite_import_line(line: str) -> str:
    """Rewrite one physical import line. Preserves indent and trailing newline."""
    nl = "\n" if line.endswith("\n") else ""
    core = line[:-1] if nl else line
    stripped = core.lstrip()
    indent = core[: len(core) - len(stripped)]

    if stripped.startswith("from "):
        m = re.match(r"from\s+(\w+)(\s+import\s+.+)$", stripped)
        if not m:
            return line
        mod, rest = m.group(1), m.group(2)
        if mod == "deckwright" or mod not in MOD_SET:
            return line
        return f"{indent}from deckwright.{mod}{rest}{nl}"

    if stripped.startswith("import "):
        # Keep trailing comments (e.g. noqa) on the rewritten line.
        code, sep, comment = stripped.partition("#")
        comment = (sep + comment) if sep else ""
        code = code.rstrip()
        body = code[len("import "):]
        parts = [p.strip() for p in body.split(",")]
        pkg: list[str] = []
        keep: list[str] = []
        for part in parts:
            m = re.match(r"^(\w+)(\s+as\s+\w+)?$", part)
            if m and m.group(1) in MOD_SET:
                pkg.append(f"{m.group(1)}{m.group(2) or ''}")
            else:
                keep.append(part)
        if not pkg:
            return line
        trail = ("  " + comment) if comment else ""
        chunks: list[str] = []
        if keep:
            chunks.append(f"{indent}import {', '.join(keep)}")
        chunks.append(f"{indent}from deckwright import {', '.join(pkg)}{trail}")
        return "\n".join(chunks) + nl

    return line


def rewrite_file_imports(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    changed = False
    for line in lines:
        if re.match(r"^\s*(import|from)\s+", line):
            new = rewrite_import_line(line)
            if new != line:
                changed = True
                out.append(new)
                continue
        out.append(line)
    if not changed:
        return False
    path.write_text("".join(out), encoding="utf-8")
    return True


def fix_repo_roots() -> None:
    """Modules that treated __file__.parent as the repo root now need parents[1]."""
    # Match ".parent" only when it is not already ".parents[N]".
    pat = re.compile(r"(Path\(__file__\)(?:\.resolve\(\))?)\.parent(?!s\b)")
    for name in REPO_PATH_FILES:
        path = PKG / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new_text, n = pat.subn(r"\1.parents[1]", text)
        if n:
            path.write_text(new_text, encoding="utf-8")
            print(f"  repo-root paths: {path.relative_to(ROOT)} ({n})")


def rewrite_tree(paths: list[Path]) -> int:
    n = 0
    for path in paths:
        if path.suffix != ".py":
            continue
        if path.name == "package_root_modules.py":
            continue
        if rewrite_file_imports(path):
            print(f"  imports: {path.relative_to(ROOT)}")
            n += 1
    return n


def patch_string_refs() -> None:
    """Path and uvicorn string updates outside import statements."""
    replacements: list[tuple[Path, list[tuple[str, str]]]] = [
        (ROOT / "run.sh", [("python app.py", "python -m deckwright")]),
        (
            ROOT / "scripts" / "check_all.py",
            [
                ('"app:app"', '"deckwright.app:app"'),
            ],
        ),
        (
            ROOT / ".cursor" / "skills" / "verify-deckwright" / "lib" / "control.mjs",
            [
                ("spawn(py, ['app.py'], {", "spawn(py, ['-m', 'deckwright'], {"),
            ],
        ),
        (
            ROOT / ".cursor" / "skills" / "verify-deckwright" / "SKILL.md",
            [
                ("`.venv/bin/python app.py`", "`.venv/bin/python -m deckwright`"),
                ("Never `pkill -f app.py`.", "Never `pkill -f deckwright`."),
            ],
        ),
        (
            ROOT / "tests" / "test_llm_max_meter.py",
            [('REPO / "app.py"', 'REPO / "deckwright" / "app.py"')],
        ),
        (
            ROOT / "tests" / "test_cardfacts.py",
            [('ROOT / "app.py"', 'ROOT / "deckwright" / "app.py"')],
        ),
        (
            ROOT / "scripts" / "api_boundary.py",
            [
                ('ROOT / "app.py"', 'ROOT / "deckwright" / "app.py"'),
                ('ROOT / "boot.py"', 'ROOT / "deckwright" / "boot.py"'),
            ],
        ),
        (
            ROOT / "tests" / "test_green_isolation.py",
            [
                ('"import app\\n"', '"from deckwright import app\\n"'),
            ],
        ),
    ]
    for path, pairs in replacements:
        if not path.exists():
            print(f"  skip missing {path}")
            continue
        text = path.read_text(encoding="utf-8")
        orig = text
        for a, b in pairs:
            text = text.replace(a, b)
        if text != orig:
            path.write_text(text, encoding="utf-8")
            print(f"  refs: {path.relative_to(ROOT)}")


def main() -> int:
    print("modules:", ", ".join(MODULES))
    git_mv_modules()
    write_package_scaffold()
    # Rewrite imports in the package, tests, and scripts.
    targets = list(PKG.glob("*.py"))
    targets += list((ROOT / "tests").rglob("*.py"))
    targets += list((ROOT / "scripts").glob("*.py"))
    print("rewriting imports…")
    rewrite_tree(targets)
    print("fixing repo-root Path(__file__) anchors…")
    fix_repo_roots()
    print("patching string refs…")
    patch_string_refs()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
