"""Where the bridge keeps its own files: config.yaml and the show data (pins.yaml, colors.yaml,
chases.yaml). Default: ~/Documents/Resolume Arena/Push 2/, next to Arena's compositions and presets
(Štefan, 2026-09-28: "next to Arena's files"). PUSH2_DATA_DIR overrides it (tests, a second machine).
A file that still sits next to the script from an earlier version is copied into the folder the
first time it is asked for, so nothing is lost when the location changes."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
DEFAULT_DIR = Path.home() / "Documents" / "Resolume Arena" / "Push 2"


def data_dir(create=True) -> Path:
    d = Path(os.environ.get("PUSH2_DATA_DIR") or DEFAULT_DIR).expanduser()
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def data_file(name, configured=None) -> Path:
    """The bridge's file `name`: an explicit path from config.yaml wins; otherwise it lives in the
    data folder, seeded once from the copy next to the script when only that exists."""
    if configured:
        return Path(configured).expanduser()
    try:
        target = data_dir() / name
    except OSError as e:                                   # no home folder: stay next to the script
        print(f"[data] can't use {DEFAULT_DIR}: {e}", file=sys.stderr)
        return REPO / name
    legacy = REPO / name
    if not target.exists() and legacy.exists():
        try:
            shutil.copy2(legacy, target)
            print(f"[data] {name} copied to {target}")
        except OSError as e:
            print(f"[data] can't copy {legacy} to {target}: {e}", file=sys.stderr)
            return legacy
    return target


def config_path(explicit=None) -> Path:
    """--config wins; otherwise <data folder>/config.yaml, seeded from the repo's config.yaml."""
    return data_file("config.yaml", explicit)
