"""paths.py: the bridge's own folder for config and show data, next to Arena's files.   python tests/test_paths.py"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402


def test_default_folder_is_next_to_arenas_files():
    os.environ.pop("PUSH2_DATA_DIR", None)
    assert paths.data_dir(create=False) == Path.home() / "Documents" / "Resolume Arena" / "Push 2"


def test_env_var_moves_the_folder_and_files_land_in_it():
    tmp = Path(tempfile.mkdtemp())
    os.environ["PUSH2_DATA_DIR"] = str(tmp / "data")
    try:
        assert paths.data_dir() == tmp / "data" and (tmp / "data").is_dir()
        assert paths.data_file("chases.yaml") == tmp / "data" / "chases.yaml"
        assert paths.data_file("chases.yaml", str(tmp / "elsewhere.yaml")) == tmp / "elsewhere.yaml", "an explicit path wins"
    finally:
        os.environ.pop("PUSH2_DATA_DIR", None)


def test_files_next_to_the_script_are_copied_once():
    tmp = Path(tempfile.mkdtemp())
    os.environ["PUSH2_DATA_DIR"] = str(tmp / "data")
    old_repo = paths.REPO
    paths.REPO = tmp / "repo"
    paths.REPO.mkdir()
    (paths.REPO / "pins.yaml").write_text("order: [a]\n")
    try:
        p = paths.data_file("pins.yaml")
        assert p == tmp / "data" / "pins.yaml" and p.read_text() == "order: [a]\n", "seeded from the script folder"
        p.write_text("order: [b]\n")
        assert paths.data_file("pins.yaml").read_text() == "order: [b]\n", "never overwritten again"
        assert not paths.data_file("colors.yaml").exists(), "no legacy file: nothing is created"
        assert paths.config_path(None) == tmp / "data" / "config.yaml"
        assert paths.config_path(str(tmp / "my.yaml")) == tmp / "my.yaml"
    finally:
        paths.REPO = old_repo
        os.environ.pop("PUSH2_DATA_DIR", None)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("pass", name)
            except Exception as e:
                failed += 1
                print("FAIL", name, "→", f"{type(e).__name__}: {e}")
    print("OK" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
