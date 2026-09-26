# Bar Chaser Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-bar-layer engine with an FFGL effect ("Bar Chaser") on each texture layer, and rework the Push's SEQ pad layout (24 pads, multi-select, patterns on row 5).

**Architecture:** `plugin/` holds a CMake project (vendored FFGL SDK lib + pugixml) building a universal `.bundle`; the effect exposes 55 parameters (see the spec) and masks its input by pad rectangles read from the Advanced Output preset. `chaser_engine.py` becomes `PluginEngine` behind the unchanged engine interface. `push_resolume_bridge.py` changes pad mapping, selection and setup actions; `sequencer.py` keeps its logic (bars are just named `pad k`).

**Tech Stack:** C++17, OpenGL 4.1 core GLSL, CMake ≥ 3.15, Xcode toolchain; Python 3.9-compatible bridge as before.

## Global Constraints

- Python parts: 3.9 syntax, pyflakes clean, every test script prints `OK`, fresh mock per script (`pkill -f mock_resolume.py; python tests/mock_resolume.py &`).
- C++: `-Werror`-clean under the SDK's warning set except deprecations; universal build `arm64;x86_64`, deployment target 10.15.
- Never touch Štefan's composition from tests. The plugin must never crash on a missing or malformed preset (empty dropdowns, transparent output, log line).
- Commit after every task, `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` last line.

---

## File structure

| Path | Responsibility |
|---|---|
| `plugin/CMakeLists.txt` | Builds `Bar Chaser.bundle` (MODULE, universal) and the `test_preset` executable |
| `plugin/Info.plist.in` | Bundle plist template (CMake `@VAR@`) |
| `plugin/build.sh` | `cmake` configure + build → `plugin/dist/Bar Chaser.bundle` |
| `plugin/ffgl-sdk/` | Vendored `resolume/ffgl` `source/lib` (`ffgl`, `ffglex`, `ffglquickstart`) + `LICENSE.md` |
| `plugin/third_party/pugixml/` | pugixml 1.14 |
| `plugin/src/Preset.h/.cpp` | Preset file lookup + parsing → `std::vector<Slice>` (name, l, t, r, b) and composition size |
| `plugin/src/BarChaser.h/.cpp` | The effect: parameters, preset (re)load, shader, rendering |
| `plugin/tests/test_preset.cpp` | Unit test for `Preset` |
| `chaser_engine.py` | `PluginEngine` (replaces `LayerEngine`) |
| `push_resolume_bridge.py` | 24 pads, multi-select, patterns row, Shift+Note / Browse setup, `--install-plugin` |
| `tests/mock_resolume.py` | fake Bar Chaser effect |
| `tests/test_engine.py`, `tests/test_fake_push.py`, `tests/render_preview.py` | updated tests |
| `README.md`, `CLAUDE.md`, `config.yaml`, `docs/ideas.md` | docs |

---

### Task 1: Plugin scaffold that loads in Arena

**Files:** create `plugin/CMakeLists.txt`, `plugin/Info.plist.in`, `plugin/build.sh`, `plugin/src/BarChaser.h/.cpp` (parameters + pass-through shader), vendor `plugin/ffgl-sdk/`, `plugin/third_party/pugixml/`.

- [ ] Vendor: copy `ffgl/source/lib/{ffgl,ffglex,ffglquickstart}` and `LICENSE.md` into `plugin/ffgl-sdk/`; pugixml 1.14 files into `plugin/third_party/pugixml/`.
- [ ] `CMakeLists.txt`: target `BarChaser` (MODULE, `BUNDLE TRUE`, `BUNDLE_EXTENSION bundle`, `OUTPUT_NAME "Bar Chaser"`, `MACOSX_BUNDLE_INFO_PLIST Info.plist.in`), sources = SDK `ffgl/*.cpp` + `ffglex/*.cpp` + `src/*.cpp` + pugixml, defines `TARGET_OS_MAC=1 NDEBUG=1`, frameworks OpenGL/Carbon/AppKit, `-Wno-deprecated-declarations`; `CMAKE_OSX_ARCHITECTURES "arm64;x86_64"`. Install step copies the bundle to `dist/`.
- [ ] `BarChaser.cpp`: `CFFGLPluginInfo` (id `"PSB1"`, name `"Bar Chaser"`, FF_EFFECT), the 55 parameters declared in spec order with `SetParamInfo*` / `SetOptionParamInfo` / `SetParamElementInfo`, `SetMinInputs(1)`, `SetMaxInputs(1)`, `InitGL` compiles a pass-through shader (modelled on the SDK's AddSubtract), `ProcessOpenGL` draws the input, `Set/GetFloatParameter`, `Set/GetTextParameter`, `GetParameterDisplay`.
- [ ] `build.sh` builds Release into `plugin/build` and copies `Bar Chaser.bundle` to `plugin/dist/`. Run it: the bundle must be universal (`file`).
- [ ] Add `plugin/build/` and `plugin/dist/` to `.gitignore`. Commit.
- [ ] **Checkpoint for Štefan:** `--install-plugin` (Task 5 adds it; for now `cp -R "plugin/dist/Bar Chaser.bundle" ~/Documents/Resolume\ Arena/Extra\ Effects/`), restart Arena, confirm "Bar Chaser" appears under Effects with the parameter list.

### Task 2: Preset parsing, dropdowns, masking shader

**Files:** create `plugin/src/Preset.h/.cpp`, `plugin/tests/test_preset.cpp`; modify `BarChaser.cpp`, `CMakeLists.txt`.

- [ ] `Preset`: `struct Slice { std::string name; float left, top, right, bottom; }`, `struct Preset { float width, height; std::vector<Slice> entries; }`, `std::string newestPreset(folder)`, `bool loadPreset(path, Preset&, std::string& error)`. Entries: screens (bbox of all slices) sorted by left,top; then `Screen / slice` for screens with more than one slice. Default folder from `$HOME/Documents/Resolume Arena/Presets/Advanced Output`.
- [ ] `test_preset.cpp` (plain asserts, `ctest`): on `tests/fixtures/preset_small.xml` expect entries `Bar A, Bar A copy, Bar B, Bar C, Bar C / 1 - 855 h3 2m grb, Bar C / second slice`, `Bar A` bbox `(145,72,175,530)`, `Bar C` `(545,70,600,1009)`, width 1920. Missing file → false with a message.
- [ ] `BarChaser`: load on construction and on `Reload` / `Preset` change; refill `Pad k` elements (`—` + entries) via `SetParamElementInfo` and `RaiseParamEvent(index, FF_EVENT_FLAG_ELEMENTS)`, keep current choices by name, default pad *k* = screen *k*.
- [ ] Shader: uniforms `vec4 rects[24]` (uv, y flipped), `float levels[24]`, `float master`, `vec2 edgeUV`, `int outside`, `int showPads`, `sampler2D InputTexture`, `vec2 MaxUV`. Factor = max over containing pads of `level × master × soft`; outside modes; Show pads paints pad colour + 3×5 digits along the longer axis.
- [ ] Build, run `ctest`, commit. **Checkpoint:** Štefan drags `Level 1` in Arena → bar 1 lights; `Show pads` shows numbers.

### Task 3: Mock Bar Chaser effect

**Files:** `tests/mock_resolume.py`.

- [ ] `effect:///video/Bar%20Chaser` → effect `{"name": "Bar Chaser", "display_name": "Bar Chaser", "bypassed", "params": {Preset: ParamString "", Reload: ParamEvent, Track: ParamChoice ["1","2","3","4"], Master: rng 1, Edge: rng 0..20, Outside: ParamChoice, "Show pads": bool, "Pad 1".."Pad 24": ParamChoice options ["—","Bar A","Bar B","Bar C"] (pads 1–3 default assigned, rest "—"), "Level 1".."Level 24": rng 0}}`. `note_opacity` also logs writes to any param named `Level n` (log entries `[t, pid, value]`; the test maps ids).
- [ ] Commit.

### Task 4: PluginEngine (bridge side)

**Files:** rewrite `chaser_engine.py`; rewrite `tests/test_engine.py`; remove layer-hiding from `push_resolume_bridge.py` (`visible_layers()` returns all layers; `pad_to_cell` back to arithmetic on the full list is fine — keep the function).

- [ ] `PluginEngine(rest, get_comp, refresh, send_level)`: `EFFECT = "Bar Chaser"`, `instances(comp=None) -> [(track 0-based, layer index, effect json)]` (layers' `video/effects` and their clips' effects), `ready(track)`, `layers_of(track)`, `pad_name(track, k)`, `set_level(track, pad_name_or_index, value)` (accepts `"pad 5"`), `all_dark()`, `add_to_layer(L) -> int` (REST add effect, refresh), `set_track(L, n)` (PUT the instance's `Track` choice by value), rate limiting as before.
- [ ] `tests/test_engine.py`: add the effect to layers 1 and 2 via `rest.add_effect(L, "Bar Chaser")`, set layer 2's Track to 2 via `eng.set_track(2, 2)`, assert `instances()`, `pad_name(0, 0) == "Bar A"`, `pad_name(0, 5) == "—"`, `set_level` writes `Level 1` on layer 1 only, rate limiting, `all_dark`.
- [ ] Delete `LayerEngine`, hidden-layer test block in `test_fake_push.py`, `--setup-chaser` from `main()`. Commit.

### Task 5: Push layout, selection, setup actions, CLI

**Files:** `push_resolume_bridge.py`, `display.py`, `tests/test_fake_push.py`, `tests/render_preview.py`, `sequencer.py` (constant `N_PADS = 24`, `pad_key(k) -> "pad k+1"`).

- [ ] Bridge: `sel_pads = {0}`; `pad_index(i, j)` for rows 5–7 → `(7 - i) * 8 + j`; `pattern_index(j, shift)` for row 4; `_seq_pad`: steps toggle on all selected (all-on → off), pads select / Shift-toggle + flash, patterns switch (queued); `_seq_button`: Shift + Note → `engine.add_to_layer(sel layer)`; Browse alone → `set_track(sel layer, seq.track + 1)`; Browse + BDn → `set_track(sel layer, n + 1)`; Octave unused; `_seq_pad_colors` per spec; LED for unassigned pads off; snapshot: `pads` names, `layer` name of the track's first instance; display line.
- [ ] `--install-plugin [PATH]` in `main()`: copy bundle to Extra Effects (`shutil.copytree`, overwrite), print "restart Arena".
- [ ] Rewrite the F18 test block per the spec's test list; `render_preview.py` uses `pad 1`/`pad 2` names and a fake instance from the mock.
- [ ] Run everything (both modes), commit.

### Task 6: Docs

- [ ] README (SEQ section: plugin install, effect panel, new pad layout), CLAUDE.md (files, control map, modes, verified APIs: effect add with a space in the name), `config.yaml` (drop the layer-engine keys, keep `sequencer.tracks`), `docs/ideas.md` F18 note, `--check`: no change. Commit, push branch.
