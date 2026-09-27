# Refactoring, code review and release analysis

Date: 2026-09-27, updated the same evening for the PRESETS / MAPPING / blackout-resend work.
Reviewed: `main` at `2990927` (PR #6 merged). `docs/seq-banks-handover/` exists only untracked in
the main checkout and was not reviewed.

## 0. Baseline

| Check | Result |
|---|---|
| `tests/test_sequencer.py` | OK (5 tests) |
| `tests/test_banks.py` | OK, 110 rigs × 16 recipes = 1,760 patterns, 0.6 s |
| `tests/test_engine.py` | OK (8 tests) |
| `tests/test_fake_push.py`, WebSocket and `TEST_POLL=1` | OK, OK |
| `plugin/build.sh` (universal bundle + ctest preset / host) | OK, 2/2 tests |
| pyflakes, Python 3.9 syntax check of every module (incl. `banks.py`, `rig.py`) | clean |

The mock tests ran on a private port (18080, copies of the scripts plus `tests/fixtures` in the
scratchpad): Arena and Štefan's bridge are live on 8080. See B3.

Size after the merge:

| | before (`a4db926`) | now (`2990927`) |
|---|---|---|
| `push_resolume_bridge.py` | 1,931 lines | 2,329 lines |
| `Bridge` methods / attributes set in `__init__` | 97 / 70 | 98 / 87 |
| Methods ≥ 60 lines | 6 | 7 (`snapshot` 130, `button` 129, `button_colors` 112, `__init__` 88, `_seq_button` 86, `_seq_pad` 70, `_seq_pad_colors` 62) |
| New pure modules | | `rig.py` 249, `banks.py` 439 |

Measured earlier on a synthetic 20-layer × 16-clip composition (650 KB JSON), Apple Silicon:
`set_comp` 16 ms, `snapshot()` 0.07 ms, LED pass 0.9 ms at 50 Hz, `render()` 3.3 ms at 20 Hz.
**Performance is not a problem.** Nothing below is about speed, except the file-write bugs.

## 1. Verdict in one paragraph

The code is in good shape for a four-day-old project, and the presets work is the best-structured
part of it: `rig.py` and `banks.py` are pure, deterministic, 3.9-clean and tested on 110 rigs. The
risk is concentration, not quality: every screen still lands in `Bridge`, which grew by 400 lines
in one merge (MAPPING, PRESETS, the blackout watchdog) and now has 98 methods, 87 attributes and a
130-line `snapshot()`. The second risk is lifecycle: `run()` is a script (`while True`, closures,
`print`, files next to the script), which is exactly what stands between "script" and "app". Four
confirmed bugs (one new, in the presets glue), a handful of smaller items, then a module plan and
the release options.

## 2. Confirmed bugs

### B1 — the chaser engine drops flashes on fast grids

`PluginEngine.set_level` (`chaser_engine.py:231`) skips a non-zero value that arrives within 20 ms
of the previous send for the same `Level n` and **never sends it later**. `Sequencer.tick()` only
reports changes, so a level that then stays constant (attack 0, sustain 1, the default envelope) is
never sent at all: the bar stays dark for the whole step. Unchanged by the merge; the Techno bank's
kicks and hats run at 1/16, so presets hit it.

With the default envelope (release 0.1 beat), gate 0.5, at 120 BPM:

| Grid | Step | Release ends at | Gap to next step | Result |
|---|---|---|---|---|
| 1/8 | 250 ms | 175 ms | 75 ms | fine |
| 1/16 | 125 ms | 112 ms | 13 ms | **second flash dropped** |

Reproduction (pure, no mock):

```python
eng = PluginEngine(None, lambda: comp, lambda: None, lambda pid, v: sent.append((pid, v)))
eng.set_level(0, "pad 1", 1.0, now=100.000)   # flash
eng.set_level(0, "pad 1", 0.0, now=100.112)   # release finished
eng.set_level(0, "pad 1", 1.0, now=100.125)   # next step, 13 ms later
# sent == [1.0, 0.0]   ← the 1.0 at 100.125 is gone
```

Fix: never drop, delay. Keep the newest skipped value in `self._pending[pid]` and add
`flush(now)` that sends pending values whose window has passed; `Bridge.seq_loop` calls
`engine.flush()` every tick (it runs at 100 Hz anyway). Simpler alternative: remove the 20 ms rule
and keep only the `busy` throttle, since `tick()` already coalesces to changes and a WebSocket `set`
is cheap. Either way add the reproduction above to `tests/test_engine.py`.

### B2 — `chases.yaml` is written on every encoder tick, on the MIDI thread

`_turn_seq` (`push_resolume_bridge.py:951`) ends with `seq.save()` unconditionally, `turn_swing`
(`:993`) too, and `_seq_pad` saves on every step press (`:811`, `:822`). `Sequencer.save()` dumps
all 16 patterns to YAML: **82 ms per call** with a full file (139 KB). Presets make full files the
normal case: one stored preset is 4 tracks × up to 9 pads × 32 steps. One knob turn of 20 ticks =
1.6 s of blocked MIDI input and 3 MB written.

Fix: `Sequencer.save()` only sets `self.dirty = True` and a timestamp; `seq_loop` (or a
`threading.Timer`) writes when `dirty` and the last change is > 0.5 s old, plus once at shutdown.
Take `to_dict()` under the lock and do the YAML dump and the file write outside it.

### B3 — the mock on 8080 hijacks a running bridge

`tests/mock_resolume.py:250` binds `127.0.0.1:8080` while Arena listens on `*:8080`. On macOS the
more specific bind wins for loopback clients, so **every process on this Mac that talks to
`127.0.0.1:8080` talks to the mock while it runs**. Seen today: Štefan's live bridge was answered
by another session's mock. Also the mock starts its server at import time (no
`if __name__ == "__main__"`), so nothing can import its fixtures.

Fix (small): `MOCK_PORT` env var / `--port` in the mock and the three test scripts, default
something that is not 8080 (e.g. 18080); `__main__` guard; `test_fake_push` sets
`cfg["resolume"]["port"]` from the same value. Then a pytest fixture can start the mock on a free
port per run (section 6).

### B4 — a preset pattern edited on the Push is still re-fitted, losing the edits (new)

The spec's rule is "placement belongs to the preset, knobs belong to the user: step edits clear
`source`". `Sequencer.mark_edited()` implements it for the sequencer's own API, but the bridge's
step toggles and Delete + step (`_seq_pad`, `push_resolume_bridge.py:808–822`) edit
`seq._steps(key)` directly and never call it. `CLAUDE.md` claims the opposite ("also in
`_seq_pad`"). Reproduction, no mock:

```python
br.seq.store_pattern(2, banks.realize("R1", rig, 4711)); br.seq.current = 2
br._seq_pad((0, 0), 100, True); br._seq_pad((0, 0), 0, False)   # step 1 toggled on the Push
br.seq.pattern.source   # still {'bank': 'techno', 'recipe': 'R1', 'seed': 4711, 'rig': …}
br.seq.toggle_step("pad 1", 5); br.seq.pattern.source           # None, as intended
```

Consequence: the next composition load with a different rig signature (`check_rig(loaded=True)`
→ `Sequencer.refit`), or a YES to the re-fit question, replaces every step of a pattern the user
has reworked on the Push. Silent data loss, and it hits exactly the people who use presets as a
starting point. Fix: give `Sequencer` a public `set_step(keys, step, on, level)` /
`remove_step(keys, step)` that marks the pattern edited, and use it from `_seq_pad` instead of
`_steps` (S16). Add a bridge-level test: toggle a step on a preset pattern, assert `source is None`.

## 3. Smaller findings

| # | Where | What | Suggest |
|---|---|---|---|
| S1 | `run()` handlers `:2117–2160` | No `try/except` around `bridge.*` calls. python-rtmidi swallows exceptions from the callback thread, so a bug leaves state half-updated with only a stderr line nobody sees in an app | One wrapper that logs with traceback |
| S2 | `run()` `:2102` | Cannot be called twice in one process: push2-python keeps the **first** registered handler per action (`trigger_action`), so a second `run()` would drive the old `Bridge`. Blocks any start/stop supervisor (section 5) | Register handlers once at import, dispatching to a `current` bridge; `PushIO.start()/stop()` |
| S3 | `Bridge.__init__`, `main()` | Data and config files resolved with `Path(__file__).with_name(...)`; `rig.PRESET_FOLDER` and `DEFAULT_CONFIG["sequencer"]["preset_folder"]` add a second machine-specific path | One `paths.py` (section 4.4); see S15 for the preset folder |
| S4 | `page_slots()`, `fx_page_items()` | Getters that clamp `self.page` / `self.fx_page`, called from `button_colors()` 50×/s | Clamp where the page changes; getters stay pure |
| S5 | `set_comp()` `:384` | Called from two threads (poll and WebSocket), now with three side effects: `sync_pads` (writes params), `check_rig` (may re-fit and write `chases.yaml`), `_check_blackout` (may resend). Works under the lock; the file write happens inside it | One `on_composition()` entry point; move the re-fit's `save()` out of the lock (B2 does that) |
| S6 | `ResolumeWS.outbox` | Unbounded queue; `seq_loop` pushes up to 100 sets/s while a stalled socket is still `live` | `queue.Queue(maxsize=…)` + drop-oldest, or coalesce by pid like `Sender.params` |
| S7 | `Checker._post_text` vs `Resolume._post_text` | Duplicate REST code; the checker also bypasses the new `_req` retry | Checker uses `Resolume` |
| S8 | `LEGACY_MODES`, `mode.setter` | Only `tests/test_engine.py` and `tests/render_preview.py` use the old names | Update the two callers, delete |
| S9 | `push_resolume_bridge.py` imports with `# noqa: F401` | The bridge module re-exports `render`, `walk`, … so tests can reach them via `B.` | Tests import from the real modules; goes away with the package |
| S10 | `Bridge.button()` `:1681` | 129 lines; the PRESETS lower row went into `_seq_button` instead, so the ladder now spans two methods | Section 4.2 |
| S11 | `print()` ×30 | Logging by `print` to a terminal that an app does not have | `logging` to `~/Library/Logs/…`, "Show log" in the menu |
| S12 | `README` "Windows and Linux may work" | Untested; `Preset.cpp` uses `dirent`/`HOME`, `rig.py` expands `~/Documents/Resolume Arena`, CMake is macOS-only, `run()` sets `DYLD_…` | Say "macOS only" until a Windows build exists |
| S13 | Test helpers | `resolve_node`, `walk`, `fmt_value`, `color_label`, `hex_to_rgba` have no direct tests; `display.render` only a visual preview | Cheap pure tests; one render smoke test per mode (there are now 9 branches in `render()`) |
| S14 | `Resolume._req` `resolume_api.py:160` | Retries every `requests.ConnectionError`: that includes *connection refused* and `ConnectTimeout` (its MRO is `ConnectTimeout → ConnectionError → Timeout`), although the docstring says timeouts are not repeated. With Arena on localhost a refusal is instant, so it is harmless; with a remote `resolume.host` that is off, every call takes 3× its timeout (a poll = 6 s) | Retry only when `e.args[0]` is a urllib3 `ProtocolError` (the "closed without response" case the fix is for); fix the docstring |
| S15 | `rig.py:37–90` vs `plugin/src/Preset.cpp` | `preset_rects`, `resolve_preset` and the folder default are a Python twin of the plugin's parser, resolver and folder: three things that must stay equal by hand, guarded only by both test suites using the same fixture. Worse, the bridge reads **its own** disk while the plugin reads **Arena's**: with `resolume.host` on another machine, or a different `Preset` text, the rig silently degrades to the virtual split (`… v`), and presets place kicks on the wrong bars with no message | Let the plugin publish its geometry: a read-only text parameter (`Geometry`: `name:x,y,w,h;…`, or one per pad) refreshed with `RaiseParamEvent` after a load, read by the bridge over REST / WS like `Preset` is today. Removes `preset_rects`, `resolve_preset`, `preset_folder`, the mtime polling and the machine assumption. To verify first: Arena serves a text param's new value after `RaiseParamEvent` (the `Preset` display text already flows) |
| S16 | `_seq_pad` `:808–822` | Reaches into `Sequencer._steps` in four places. This is how B4 happened, and it will happen again with the next flag on `Pattern` | Public `Sequencer.set_step` / `remove_step`; `_steps` stays private |
| S17 | `current_rig()` `:583` | `os.path.getmtime` and possibly an XML parse from the LED path (`_seq_pad_colors`, `button_colors`) under the lock, throttled to once a second. Fine today; disappears with S15 | — |
| S18 | Docs | `CLAUDE.md` says `mark_edited` is called in `_seq_pad` (it is not, B4); `docs/seq-banks-handover/` is untracked | Fix the sentence with B4; commit or delete the folder |

Reviewed and fine: the blackout watchdog (`_check_blackout` `:1126`, `blackout_watchdog` `:1164`)
decides on the value Resolume reports, never on the display override, bounds its resends, and lets
the master knob take over; `_req` is correct for the case it was written for; the shared pad
mapping (`sync_pads`, `set_pad`) has the right precedence (loaded composition → T1, else edits in
Arena, own writes pending); `banks.Draw.hop` degrades its constraints in a sensible order; the
rig's virtual split table in the spec matches `virtual_positions()`. Threading otherwise looks
right: all MIDI output in the main thread, `Sender` coalesces params, `overrides` prevent jitter,
`seq_loop` survives exceptions, the lock is held for computation and not for cairo.

## 4. Refactoring plan

Goal: the same behaviour, `test_fake_push.py` untouched and green after every step, Python 3.9 kept.
Sizes: S ≈ an hour, M ≈ a session.

### 4.1 Target layout

```
push2resolume/                 (package; `python -m push2resolume` = today's script)
  app.py          main(), CLI (--dump, --check, --install-plugin, --sim), load_config
  paths.py        where config / pins / colors / chases / log live (repo folder or app data dir)
  bridge.py       Bridge core: comp + index, selection, overrides, value_of, _set / _nudge,
                  desired_ids, beat clock, blackout + watchdog, flash, screen + overlay state
  screens/
    params.py     slots, pages, Convert-move, pins order                (~250 lines today)
    color.py      colour params, palette, paste, master colour           (~200)
    fx.py         effect lists                                          (~60)
    mix.py        mix / mute / solo overlay                             (~60)
    seq.py        steps, patterns, pads, groups, knobs, LEDs, seq_loop, join / add chaser (~500)
    seq_presets.py  preset pick, store, questions, re-fit offer, rig line (~200: current_rig,
                    check_rig, _store_preset, _preset_button, _preset_pattern_pad, _presets_snapshot)
    seq_mapping.py  fixtures ↔ pads, Octave pages (~150: _map_pad, _map_pad_colors, mapping snapshot)
  push_io.py      PushIO: handler registration (once), LED diff, palette, display frame, start/stop
  resolume/
    json.py       resolve_node, walk, text, colour helpers  (from resolume_api.py)
    rest.py       Resolume client (+ _req)
    sender.py     Sender
    ws.py         ResolumeWS
  sequencer.py, chaser_engine.py, banks.py, rig.py, display.py   unchanged
```

`display.py` gets one `draw_<screen>(ctx, snap)` per screen in the same move as 4.2; it is already
structured that way inside one function, and the PRESETS branch that went in ahead of the generic
SEQ branch shows the chain is getting order-sensitive.

### 4.2 The screen split (the important one)

Every screen implements the same five hooks; `Bridge` keeps the state and dispatches:

```python
class Screen:
    knobs   = ()                                  # labels for the display
    def turn(self, br, idx, inc): ...             # K1–K8
    def button(self, br, name, down) -> bool: ... # True = consumed
    def pad(self, br, ij, velocity, down) -> bool: ...
    def leds(self, br, out): ...                  # fills button colours for its rows
    def snapshot(self, br) -> dict: ...           # its part of the display dict
```

The presets merge is the proof that this is the natural shape: MAPPING already exists as
`_map_pad` (pad), Octave handling (button), `_map_pad_colors` (leds) and a `mapping` dict in
`snapshot()`; PRESETS as `_preset_button`, `_preset_pattern_pad`, a lower-row block in
`button_colors()` and `_presets_snapshot()`. They are screens spread over five methods of `Bridge`,
each guarded by `if self.mapping()` / `if self.presets_menu()`. Moving them is mechanical.

`Bridge.button()` shrinks to: global buttons (Shift, overlays, view switch, tap, blackout,
navigation) → `overlay.button()` → `screen.button()`. `button_colors()` and `snapshot()` become
"global part + screen part". New menus are new files, not new `elif`s.

### 4.3 Order of work

| Step | What | Size | Why first |
|---|---|---|---|
| 1 | B1, B2, B3, B4 (+ S16, which is B4's fix) | S each | Bugs; B4 loses user data; B3 unblocks parallel sessions |
| 2 | `paths.py` + `logging` (S3, S11) | S | Prerequisite for any packaging; no behaviour change |
| 3 | Package skeleton: move files, keep `push_resolume_bridge.py` as a 3-line shim | S | Imports in tests change once |
| 4 | Extract SEQ into `screens/seq.py`, `seq_presets.py`, `seq_mapping.py` | M | The presets work has landed, so the ~900-line SEQ cluster can move now; it is the biggest and the most self-contained |
| 5 | `Screen` dispatch for params / color / fx / mix | M | Kills `button()`; `display.py` split in the same step |
| 6 | `PushIO` with `start()/stop()`, handlers registered once (S1, S2) | M | Needed for the supervisor in section 5 |
| 7 | pytest + mock fixture on a free port, CI | S–M | Section 6 |
| 8 | `pyproject.toml`, pinned push2-python commit, no simulator deps | S | Section 5.4 |
| 9 | Geometry from the plugin (S15) | M (plugin + bridge) | Removes the last local-disk dependency of SEQ and the parser twin; can run in parallel with 4–6 |

Rule for the transition: `Bridge`'s public surface used by the tests (`button`, `turn`, `pad_pressed`,
`snapshot`, `button_colors`, `pad_colors`, `mode`, `page`, `sel`) stays, so `test_fake_push.py` is the
safety net for every step.

## 5. Release: "run Arena and the Push just works"

Split the wish into what it actually needs:

1. Something runs **before or when Arena starts**, without a Terminal, venv or Homebrew.
2. It **takes the Push only while Arena runs** and releases it otherwise. Ableton Live and the
   bridge cannot share the Push display (one USB interface claim); Live must keep working.
3. It shows the user that it is alive, and where the log is.
4. Settings without editing YAML; the show data (pins, colours, chases) in a user folder.
5. Installable and updatable by someone who is not Štefan.

### 5.1 Options

**A. Resident agent (recommended).** One `Push2Resolume.app` in the menu bar, started at login
(a LaunchAgent plist in `~/Library/LaunchAgents`, or `SMAppService` on macOS 13+ from a "Start at
login" checkbox). It idles until Arena is running and a Push is plugged in, then starts the bridge;
it stops the bridge and releases the Push when Arena quits or Live starts.

- Arena detection: `NSWorkspace` launch / terminate notifications via PyObjC for
  `com.resolume.arena` (verified bundle id in `/Applications/Resolume Arena/Arena.app`), or simply
  poll `GET /api/v1/product` on 8080 every 2 s, which the bridge does already.
- Push detection: pyusb `find(idVendor=0x2982, idProduct=0x1967)` every 2 s; MIDI port
  `Ableton Push 2 Live Port`.
- Live detection: `com.ableton.live` running → release the Push (close MIDI ports, dispose the
  USB endpoint, black frame first).
- Menu: status (Arena / Push / link LIVE-POLL), Open settings, Show log, Install Bar Chaser
  effect, Start at login, Quit.
- Effort: spike M (PyInstaller build that runs), polish M–L (menu, login item, pkg, signing).
- Needs step 6 of the refactoring (start/stop), nothing else.

**B. Plugin-launched helper.** The Bar Chaser bundle spawns the bridge when Arena loads it. Arena
loads the bundle in-process at startup (its log: `scanDirectoryForExtensions … Loading plugin …
Bar Chaser … successfully loaded`, before any composition), so a static constructor in the dylib
runs at Arena start. It is loaded 4× (scan, then per instance), so the spawn must be idempotent
(pid file / lock). The helper exits when Arena's PID dies.
Pros: nothing extra to install, lifecycle = Arena's. Cons: a plugin that spawns processes is
unusual and unsupported by Resolume; a crash in the constructor gets the plugin blacklisted; the
helper is still a packaged Python app (~80 MB inside the bundle), so **A's packaging work is a
prerequisite anyway**. Verdict: an optional 50-line convenience on top of A, not a replacement.

**C. Native rewrite inside the plugin.** The FFGL bundle itself talks to the Push (libusb, CoreMIDI)
and to Arena's REST / WebSocket on localhost; display drawn with CoreGraphics. One signed bundle,
no Python at all, the cleanest UX. Cost: rewriting ~4,500 lines of Python in C++ (JSON, WebSocket,
threads inside a render plugin, a singleton across instances, and now the recipes), weeks, and the
iteration speed is gone. Only if this becomes a product for other people.

**Recommendation: A**, phased as below, with B as a possible later add-on.

### 5.2 Phases

| Phase | Deliverable | Size |
|---|---|---|
| 0 | B1–B4, `paths.py`, `logging` | S |
| 1 | `PushIO.start()/stop()` + a supervisor loop in the same process: the bridge runs forever, attaches to the Push while Arena answers, detaches otherwise. Štefan gets "run Arena and it works" today with a LaunchAgent plist that calls `.venv/bin/python -m push2resolume`. This is `F17` in `docs/ideas.md` | M |
| 2 | PyInstaller `.app`, menu bar icon (rumps), Start at login, `.pkg` that also drops `Bar Chaser.bundle` into Extra Effects, signing + notarization | M–L |
| 3 | Settings page, geometry from the plugin (S15), plugin-launched helper (optional), Windows | later |

### 5.3 Packaging facts to plan with

- **PyInstaller onedir → .app.** Add `libusb-1.0.dylib` and cairo's dylibs with `--add-binary`;
  `run()` already sets `DYLD_FALLBACK_LIBRARY_PATH` for Homebrew, `paths.py` points it at the
  bundle's `Frameworks/` instead when frozen (`sys.frozen`).
- **push2-python** is installed from git and drags in flask, flask-socketio, eventlet (deprecated,
  prints warnings at import) and pillow, all for its browser simulator. Pin the commit and install
  `--no-deps` with mido, python-rtmidi, pyusb, numpy listed explicitly, or vendor a trimmed copy
  (2.6 k lines, MIT).
- **Architecture:** arm64 first. A universal2 app means universal wheels for numpy, pycairo,
  python-rtmidi; not worth it for phase 2.
- **Gatekeeper:** an unsigned `.app` or `.bundle` downloaded from GitHub is blocked ("developer
  cannot be verified"). Either Developer ID + notarization (Apple developer account, 99 USD/yr, covers
  the plugin too) or a documented `xattr -dr com.apple.quarantine`. Decide before phase 2.
- **Data folder:** `~/Library/Application Support/Push2Resolume/` for config, pins, colours, chases
  and the log, or `~/Documents/Resolume Arena/Push 2/` next to the presets Arena users already know.
  First run copies the files from the repo folder if they exist there. The Advanced Output folder
  (`rig.PRESET_FOLDER`) is the one path that must stay Resolume's, and only until S15 is done.
- **Updates:** a "Check for updates" menu item against the GitHub releases API is enough; Sparkle is
  overkill.
- **Windows** is where most Resolume users are, but not where Štefan's rig is: Push display needs a
  WinUSB driver (Zadig) when Live is not installed, the plugin needs a Windows CMake target and a
  `Preset.cpp` without `dirent`/`HOME`, `rig.py` needs the Windows Documents path (or S15), the agent
  becomes a tray icon. L, later.

### 5.4 Settings without YAML

Most settings already live on the Push (pins, colours, chases, the pad mapping and presets are all
written by the bridge). What is left: Resolume host / port, grid offsets, encoder steps, per-layer
slot lists, the optional `sequencer.rig` override. A small local web page served by the bridge
(stdlib `http.server`, `http://localhost:6129`, opened from the menu) covers all of it, needs no GUI
toolkit, and works on Windows later. The Push display keeps doing first-run guidance ("Waiting for
Resolume — enable Webserver in Preferences" exists; add "Bar Chaser not installed — install it from
the menu"; the PRESETS rig line already explains why presets are unavailable).

The one thing that cannot be automated: Arena's Webserver must be enabled once in Preferences.
Detect it (connection refused on 8080) and show the instruction on the Push and in the menu.

## 6. Tests and CI

- `tests/test_banks.py` is the model: pure, fast, wide (110 rigs), independent of the mock. Keep
  writing the SEQ logic that way.
- Convert the three mock scripts to pytest. A fixture starts `mock_resolume.py` on a free port as a
  subprocess and passes host / port to the bridge config; `test_fake_push.py` (now 562 lines, one
  script whose later sections depend on the mock state left by earlier ones) becomes several test
  functions sharing one running bridge. Nothing changes in what is tested.
- GitHub Actions: `macos-latest` runs the Python tests and `plugin/build.sh` (the GL host test needs a
  GPU context; mark it skippable on CI if it fails there), `ubuntu-latest` runs the pure tests with
  a real Python 3.9 interpreter plus pyflakes, so the 3.9 constraint is enforced by CI instead of by
  memory.
- Add the pure tests from S13, the B1 reproduction, and the B4 bridge-level test.

## 7. Docs

`CLAUDE.md` (300+ lines) doubles as the handover and duplicates README's control tables; keep it,
but move the "Control map" and "Modes" detail into `docs/` and link, and fix the `mark_edited`
sentence (B4). `docs/plans/2026-09-24-step-sequencer.md` (2,141 lines) is done work; archive or
delete, git keeps it. Commit or drop `docs/seq-banks-handover/`.
