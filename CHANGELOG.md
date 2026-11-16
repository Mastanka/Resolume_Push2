# Changelog

Versions are MAJOR.MINOR.PATCH: PATCH = fixes, MINOR = new features, MAJOR = changes that need
your show files or Arena setup redone. The version lives in `push_resolume_bridge.py`
(`__version__`, `--version`). `Release/` holds the latest release, made by `python tools/make_release.py`.

## 1.0.0 (2026-11-16)

First versioned release.

- **CLIP view:** the deck on the pads; select, launch (Play), stop a layer (Record), launch a column,
  flash rows, blackout (Stop Clip), mute and solo.
- **Menus:** clip and layer parameters with your own order (Convert), COLOR with your own palette, a
  master colour and colour paste, clip and layer effects, the MIX / MUTE / SOLO screens, tempo (tap,
  resync, BPM knob).
- **SEQUENCER view:** 16 patterns × 4 tracks with ADSR, pad groups and multi-select. Each pattern
  has its own grid. Queued patterns and a waiting Apply start on their own phrase, counted from Play.
- **PRESETS:** 16 Techno recipes with macros, seed and preview; Apply, Revert, Re-fit and Load bank.
- **MAPPING:** one pad → fixture mapping shared by every Bar Chaser; kick bars.
- **Bar Chaser** FFGL effect (universal bundle). Tracks fold onto a partner. A Bar Chaser on a clip
  counts only while that clip plays.
- Settings and show data live in `~/Documents/Resolume Arena/Push 2/`.
- push2-python is pinned to the commit used in testing.

Not yet checked on the rig: the changes from PR #7 (phrases, clip-level Bar Chaser, grid per
pattern, stuck-state fixes). They pass the simulator, the mock and the fuzzers.
