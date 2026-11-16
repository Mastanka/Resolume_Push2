#!/usr/bin/env python3
"""Builds Release/: everything needed to run the bridge, at the version in push_resolume_bridge.py.

    python tools/make_release.py                 # builds the Bar Chaser plugin first (plugin/build.sh)
    python tools/make_release.py --skip-plugin   # reuse plugin/dist/Bar Chaser.bundle as it is

Release/ is generated: everything in it except a .venv is rewritten each time, so edit the project,
never Release/. A new version: bump __version__, add a CHANGELOG.md entry, commit, run this, commit
Release/. The modules are found by following the imports from push_resolume_bridge.py."""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "Release"
ENTRY = "push_resolume_bridge"
FILES = ["config.yaml", "pins.yaml", "requirements.txt", "LICENSE", "CHANGELOG.md", "PUSH2_LAYOUT.png"]
BUNDLE = ROOT / "plugin" / "dist" / "Bar Chaser.bundle"
KEEP = {".venv"}                                     # survives a rebuild (made by setup.sh)

SETUP = """#!/bin/sh
# One-time setup of this folder: a Python environment with the bridge's packages in .venv.
# Needs Homebrew's libraries first:  brew install libusb cairo pkg-config git
set -e
cd "$(dirname "$0")"
for lib in libusb cairo; do
  brew list "$lib" >/dev/null 2>&1 || { echo "missing $lib: brew install libusb cairo pkg-config git"; exit 1; }
done
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo "ready: ./start.command (or double-click it in Finder)"
"""

START = """#!/bin/sh
# Starts the bridge: double-click in Finder, or ./start.command [--sim | --dump 3 | --install-plugin | ...]
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || ./setup.sh || exit 1
exec .venv/bin/python push_resolume_bridge.py "$@"
"""

HEADER = """# Push 2 → Resolume Arena {version}

Release folder, built from commit `{commit}` ({date}){dirty}. It holds what the bridge needs to run:
the scripts, the default settings, the prebuilt **Bar Chaser** effect and this manual. Tests and
sources are in the project. Changes: [CHANGELOG.md](CHANGELOG.md).

## Install from this folder (macOS)

1. Once: `brew install libusb cairo pkg-config git` (see the detailed installation below for Homebrew).
2. Once: `./setup.sh` creates the Python environment in `.venv` and installs the packages.
3. Once per version: `./start.command --install-plugin` copies `Bar Chaser.bundle` into Arena's
   Extra Effects. Restart Arena afterwards.
4. In Arena: **Preferences → Webserver → Enable Webserver & REST API** (port 8080). Close Ableton Live.
5. Run: `./start.command`, or double-click it in Finder. Ctrl+C quits.

Your settings and show data stay in `~/Documents/Resolume Arena/Push 2/`; a new version doesn't
touch them. `config.yaml` and `pins.yaml` here are only copied there when it has none yet.

---

"""


def version():
    m = re.search(r'^__version__ = "([^"]+)"', (ROOT / f"{ENTRY}.py").read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit(f"no __version__ in {ENTRY}.py")
    return m.group(1)


def modules():
    """The project's modules the entry point imports, directly or through others."""
    local = {p.stem for p in ROOT.glob("*.py")}
    seen, todo = set(), [ENTRY]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for node in ast.walk(ast.parse((ROOT / f"{name}.py").read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                todo += [a.name.split(".")[0] for a in node.names if a.name.split(".")[0] in local]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in local:
                    todo.append(node.module.split(".")[0])
    return sorted(seen)


def git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def clean(out):
    """Empty Release/ except KEEP. Refuses a folder that isn't one of ours."""
    if not out.exists():
        out.mkdir()
        return
    entries = [p for p in out.iterdir() if p.name not in KEEP and p.name != ".DS_Store"]
    if entries and not (out / "VERSION").exists():
        sys.exit(f"{out} exists but has no VERSION file: not a release folder, not touching it")
    for p in entries:
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()


def smoke_test(out, mods, ver):
    """The release runs on its own: every module imports from Release/ alone, --version answers."""
    env = dict(os.environ, PUSH2_DATA_DIR=tempfile.mkdtemp(prefix="push2-release-"), PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    code = "import sys; sys.path[:0] = [sys.argv[1]]; import " + ", ".join(mods) + "; " \
           "import paths; assert paths.REPO == __import__('pathlib').Path(sys.argv[1]).resolve(), paths.REPO"
    for cmd in ([sys.executable, "-s", "-c", code, str(out)],
                [sys.executable, "-s", str(out / f"{ENTRY}.py"), "--version"]):
        r = subprocess.run(cmd, cwd=tempfile.gettempdir(), env=env, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"release smoke test failed: {' '.join(cmd[:3])}\n{r.stdout}{r.stderr}")
    if ver not in r.stdout + r.stderr:
        sys.exit(f"--version printed {r.stdout + r.stderr!r}, expected {ver}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-plugin", action="store_true", help="don't rebuild the Bar Chaser plugin")
    args = ap.parse_args()

    ver = version()
    if not args.skip_plugin:
        print("building the Bar Chaser plugin (plugin/build.sh) ...")
        subprocess.run([str(ROOT / "plugin" / "build.sh")], check=True)
    if not (BUNDLE / "Contents" / "MacOS").is_dir():
        sys.exit(f"{BUNDLE} missing: run plugin/build.sh")

    mods = modules()
    clean(OUT)
    for m in mods:
        shutil.copy2(ROOT / f"{m}.py", OUT / f"{m}.py")
    for f in FILES:
        shutil.copy2(ROOT / f, OUT / f)
    (OUT / "docs" / "promo").mkdir(parents=True)
    for png in sorted((ROOT / "docs" / "promo").glob("*.png")):          # the README's pictures
        shutil.copy2(png, OUT / "docs" / "promo" / png.name)
    shutil.copytree(BUNDLE, OUT / BUNDLE.name, symlinks=True, ignore=shutil.ignore_patterns(".DS_Store"))

    dirty = git("status", "--porcelain", "--", ".", ":(exclude)Release")
    (OUT / "README.md").write_text(HEADER.format(
        version=ver, commit=git("rev-parse", "--short", "HEAD") or "unknown",
        date=git("log", "-1", "--format=%cs") or "?",
        dirty=", plus changes not yet committed" if dirty else "") +
        (ROOT / "README.md").read_text(encoding="utf-8"), encoding="utf-8")
    (OUT / "VERSION").write_text(ver + "\n", encoding="utf-8")
    for name, text in (("setup.sh", SETUP), ("start.command", START)):
        (OUT / name).write_text(text, encoding="utf-8")
        (OUT / name).chmod(0o755)

    smoke_test(OUT, mods, ver)
    files = [p for p in OUT.rglob("*") if p.is_file() and not any(k in p.parts for k in KEEP)]
    size = sum(p.stat().st_size for p in files) / 1e6
    print(f"Release/ {ver}: {len(mods)} modules, {len(files)} files, {size:.1f} MB"
          + ("  (built with uncommitted changes)" if dirty else ""))


if __name__ == "__main__":
    main()
