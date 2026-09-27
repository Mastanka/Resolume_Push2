# Refactoring, code review and release analysis

Date: 2026-09-27. Reviewed: `main` at `a4db926`. Not reviewed: the `seq` branch (2 commits ahead:
MAPPING menu, blackout resend) and its uncommitted banks / rig / presets work, which another session
is doing right now. Everything below is written so that it can be done **after** that work lands.

## 0. Baseline

| Check | Result |
|---|---|
| `tests/test_sequencer.py` | OK (4 tests) |
| `tests/test_engine.py` | OK (6 tests) |
| `tests/test_fake_push.py`, WebSocket and `TEST_POLL=1` | OK, OK |
| `plugin/build.sh` (universal bundle + ctest preset / host) | OK, 2/2 tests, 8 s |
| pyflakes, Python 3.9 syntax check of every module | clean |

The mock tests ran on a private port (18080, copies of the scripts in the scratchpad): the shared
port 8080 was taken by the other session's mock. See B3, that is a real problem, not a nuisance.

Measured on a synthetic 20-layer × 16-clip composition (650 KB JSON), Apple Silicon:

| Per call | Cost | Rate |
|---|---|---|
| `Bridge.set_comp` (index rebuild) | 16 ms | every 5 s live, 4×/s in poll mode |
| `snapshot()` | 0.07 ms | 20×/s |
| `button_colors()` + `pad_colors()` | 0.9 ms | 50×/s |
| `render()` (cairo) | 3.3 ms | 20×/s |
| `desired_ids()` | 0.08 ms | 2×/s |

**Performance is not a problem.** Nothing below is about speed, except the two file-write bugs.

## 1. Verdict in one paragraph

The code is in good shape for a four-day-old project: clear names, a pure sequencer, a real plugin
test host, specs per feature, an end-to-end test without hardware. The risk is concentration, not
quality: `push_resolume_bridge.py` is 1931 lines, `Bridge` has 97 methods and 70 attributes,
`button()` is a 130-line `elif` chain, and every new screen (SEQ, MAPPING, PRESETS) lands in the same
class. The second risk is lifecycle: `run()` is a script (`while True`, closures, `print`, files next
to the script). That is exactly what stands between "script" and "app". Two confirmed bugs, one
process problem, a handful of smaller items, then a module plan and the release options.

## 2. Confirmed bugs

### B1 — the chaser engine drops flashes on fast grids

`PluginEngine.set_level` (`chaser_engine.py:139`) skips a non-zero value that arrives within 20 ms of
the previous send for the same `Level n` and **never sends it later**. `Sequencer.tick()` only reports
changes, so a level that then stays constant (attack 0, sustain 1, the default envelope) is never sent
at all: the bar stays dark for the whole step.

When it happens, with the default envelope (release 0.1 beat), gate 0.5, at 120 BPM:

| Grid | Step | Release ends at | Gap to next step | Result |
|---|---|---|---|---|
| 1/8 | 250 ms | 175 ms | 75 ms | fine |
| 1/16 | 125 ms | 112 ms | 13 ms | **second flash dropped** |
| 1/32 | 62 ms | 81 ms | (overlaps) | retrigger path, different case |

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
`engine.flush()` every tick (it runs at 100 Hz anyway). Simpler alternative: remove the 20 ms rule and
keep only the `busy` throttle, since `tick()` already coalesces to changes and a WebSocket `set` is
cheap. Either way add the reproduction above to `tests/test_engine.py`.

### B2 — `chases.yaml` is written on every encoder tick, on the MIDI thread

`_turn_seq` (`push_resolume_bridge.py:699`) ends with `seq.save()` unconditionally, `turn_swing`
(`:741`) too, and `_seq_pad` saves on every step press. `Sequencer.save()` dumps all 16 patterns to
YAML: **82 ms per call** with a full file (139 KB, every pattern used). One knob turn of 20 ticks =
1.6 s of blocked MIDI input and 3 MB written. With a mostly empty file it is a few ms, so it does
not show yet; it will the first time a show has real patterns.

Fix: `Sequencer.save()` only sets `self.dirty = True` and a timestamp; `seq_loop` (or a
`threading.Timer`) writes when `dirty` and the last change is > 0.5 s old, plus once at shutdown.
Take `to_dict()` under the lock and do the YAML dump and the file write outside it.

### B3 — the mock on 8080 hijacks a running bridge

`tests/mock_resolume.py:242` binds `127.0.0.1:8080` while Arena listens on `*:8080`. On macOS the
more specific bind wins for loopback clients, so **every process on this Mac that talks to
`127.0.0.1:8080` talks to the mock while it runs**. Seen today: Štefan's live bridge (PID 73127,
started from the main checkout) was answered by the other session's mock. With two sessions
developing in parallel this will happen again. Also the mock starts its server at import time
(no `if __name__ == "__main__"`), so nothing can import its fixtures.

Fix (small): `MOCK_PORT` env var / `--port` in the mock and the three test scripts, default
something that is not 8080 (e.g. 18080); `__main__` guard; `test_fake_push` sets
`cfg["resolume"]["port"]` from the same value. Then a pytest fixture can start the mock on a free
port per run (section 5).

## 3. Smaller findings

| # | Where | What | Suggest |
|---|---|---|---|
| S1 | `run()` handlers `:1720–1760` | No `try/except` around `bridge.*` calls. python-rtmidi swallows exceptions from the callback thread, so a bug leaves state half-updated with only a stderr line nobody sees in an app | One wrapper that logs with traceback; the lock is an RLock, so a failed handler cannot deadlock, but a `pressed`/`held_*` set may stay stale |
| S2 | `run()` `:1705` | Cannot be called twice in one process: push2-python keeps the **first** registered handler per action (`trigger_action`), so a second `run()` would drive the old `Bridge`. Blocks any start/stop supervisor (section 4) | Register handlers once at import, dispatching to a `current` bridge; `PushIO.start()/stop()` |
| S3 | `Bridge.__init__` `:213,234,243`, `main()` `:1904` | Data and config files resolved with `Path(__file__).with_name(...)`. Inside an `.app` that folder is read-only and signed | One `paths.py` (section 4.4) |
| S4 | `page_slots()` `:888`, `fx_page_items()` | Getters that clamp `self.page` / `self.fx_page`, called from `button_colors()` 50×/s. Harmless, but state mutation in a read path is how the next race is born | Clamp where the page changes; getters stay pure |
| S5 | `set_comp()` `:358` | Called from two threads (poll and WebSocket) and has side effects (`engine.sync_pads` may write params). Works under the lock; worth a comment and a single entry point that also owns the "new composition" event | Keep, document; becomes `Bridge.on_composition()` |
| S6 | `ResolumeWS.outbox` `resolume_api.py:275` | Unbounded queue; `seq_loop` pushes up to 100 sets/s while a stalled socket is still `live` | `queue.Queue(maxsize=…)` + drop-oldest, or coalesce by pid like `Sender.params` |
| S7 | `Checker._post_text` `resolume_check.py:231` vs `Resolume._post_text` `resolume_api.py:183` | Duplicate REST code; the checker has its own `req` too | Checker uses `Resolume` |
| S8 | `LEGACY_MODES` `:124`, `mode.setter` | Only `tests/test_engine.py` and `tests/render_preview.py` use the old names | Update the two callers, delete |
| S9 | `push_resolume_bridge.py` imports with `# noqa: F401` | The bridge module re-exports `render`, `walk`, … so tests can reach them via `B.` | Tests import from the real modules; goes away with the package |
| S10 | `Bridge.button()` `:1322` | 130 lines: Shift, overlays, SEQ, Play/Record/Duplicate, paste, flash, columns, Convert, tap, blackout, navigation, menus, lower row × 5 modes | Section 4.2 |
| S11 | `print()` ×28 | Logging by `print` to a terminal that an app does not have | `logging` to `~/Library/Logs/…`, "Show log" in the menu |
| S12 | `README` "Windows and Linux may work" | Untested; `Preset.cpp` uses `dirent`/`HOME`, CMake is macOS-only, `run()` sets `DYLD_…` | Say "macOS only" until a Windows build exists |
| S13 | Test helpers | `resolve_node`, `walk`, `fmt_value`, `color_label`, `hex_to_rgba` have no direct tests; `display.render` only a visual preview | Cheap pure tests; one render smoke test per mode |

Threading otherwise looks right: all MIDI output in the main thread, `Sender` coalesces params,
`overrides` prevent jitter, `seq_loop` survives exceptions, the lock is held for computation and not
for cairo. Not worth touching.

## 4. Refactoring plan

Goal: the same behaviour, `test_fake_push.py` untouched and green after every step, Python 3.9 kept.
Sizes: S ≈ an hour, M ≈ a session.

### 4.1 Target layout

```
push2resolume/                 (package; `python -m push2resolume` = today's script)
  app.py          main(), CLI (--dump, --check, --install-plugin, --sim), load_config
  paths.py        where config / pins / colors / chases / log live (repo folder or app data dir)
  bridge.py       Bridge core: comp + index, selection, overrides, value_of, _set / _nudge,
                  desired_ids, beat clock, blackout / flash, screen + overlay state
  screens/
    params.py     slots, pages, Convert-move, pins order          (~250 lines today)
    color.py      colour params, palette, paste, master colour     (~200)
    fx.py         effect lists                                    (~60)
    mix.py        mix / mute / solo overlay                       (~60)
    seq.py        pads, buttons, knobs, groups, LEDs, seq_loop, join/add chaser  (~500)
  push_io.py      PushIO: handler registration (once), LED diff, palette, display frame, start/stop
  resolume/
    json.py       resolve_node, walk, text, colour helpers  (from resolume_api.py)
    rest.py       Resolume client
    sender.py     Sender
    ws.py         ResolumeWS
  sequencer.py, chaser_engine.py, display.py   unchanged
```

`display.py` gets one `draw_<screen>(ctx, snap)` per screen in the same move as 4.2; it is already
structured that way inside one function.

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

`Bridge.button()` shrinks to: global buttons (Shift, overlays, view switch, tap, blackout,
navigation) → `overlay.button()` → `screen.button()`. The `elif name in LOWER_ROW and self.mode …`
ladder disappears because each screen owns its lower row. `button_colors()` and `snapshot()` become
"global part + screen part". New menus (MAPPING, PRESETS, BANKS) are new files, not new `elif`s.

### 4.3 Order of work

| Step | What | Size | Why first |
|---|---|---|---|
| 1 | B1, B2, B3 | S each | Bugs; B3 unblocks parallel sessions |
| 2 | `paths.py` + `logging` (S3, S11) | S | Prerequisite for any packaging; no behaviour change |
| 3 | Package skeleton: move files, keep `push_resolume_bridge.py` as a 3-line shim | S | Imports in tests change once |
| 4 | Extract SEQ into `screens/seq.py` | M | Biggest self-contained block; **do it right after the presets / banks work lands, not before** |
| 5 | `Screen` dispatch for params / color / fx / mix | M | Kills `button()`; `display.py` split in the same step |
| 6 | `PushIO` with `start()/stop()`, handlers registered once (S1, S2) | M | Needed for the supervisor in section 5 |
| 7 | pytest + mock fixture on a free port, CI | S–M | Section 6 |
| 8 | `pyproject.toml`, pinned push2-python commit, no simulator deps | S | Section 5.4 |

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
Bar Chaser … successfully loaded` at 21:36:38, before any composition), so a static constructor in
the dylib runs at Arena start. It is loaded 4× (scan, then per instance), so the spawn must be
idempotent (pid file / lock). The helper exits when Arena's PID dies.
Pros: nothing extra to install, lifecycle = Arena's. Cons: a plugin that spawns processes is
unusual and unsupported by Resolume; a crash in the constructor gets the plugin blacklisted; the
helper is still a packaged Python app (~80 MB inside the bundle), so **A's packaging work is a
prerequisite anyway**. Verdict: an optional 50-line convenience on top of A, not a replacement.

**C. Native rewrite inside the plugin.** The FFGL bundle itself talks to the Push (libusb, CoreMIDI)
and to Arena's REST / WebSocket on localhost; display drawn with CoreGraphics. One signed bundle,
no Python at all, the cleanest UX. Cost: rewriting ~3,500 lines of Python in C++ (JSON, WebSocket,
threads inside a render plugin, a singleton across instances), weeks, and the iteration speed is
gone. Only if this becomes a product for other people.

**Recommendation: A**, phased as below, with B as a possible later add-on.

### 5.2 Phases

| Phase | Deliverable | Size |
|---|---|---|
| 0 | B1–B3, `paths.py`, `logging` | S |
| 1 | `PushIO.start()/stop()` + a supervisor loop in the same process: the bridge runs forever, attaches to the Push while Arena answers, detaches otherwise. Štefan gets "run Arena and it works" today with a LaunchAgent plist that calls `.venv/bin/python -m push2resolume`. This is `F17` in `docs/ideas.md` | M |
| 2 | PyInstaller `.app`, menu bar icon (rumps), Start at login, `.pkg` that also drops `Bar Chaser.bundle` into Extra Effects, signing + notarization | M–L |
| 3 | Settings page, plugin-launched helper (optional), Windows | later |

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
  First run copies the files from the repo folder if they exist there.
- **Updates:** a "Check for updates" menu item against the GitHub releases API is enough; Sparkle is
  overkill.
- **Windows** is where most Resolume users are, but not where Štefan's rig is: Push display needs a
  WinUSB driver (Zadig) when Live is not installed, the plugin needs a Windows CMake target and a
  `Preset.cpp` without `dirent`/`HOME`, the agent becomes a tray icon. L, later.

### 5.4 Settings without YAML

Most settings already live on the Push (pins, colours, chases are written by the bridge). What is
left: Resolume host / port, grid offsets, encoder steps, per-layer slot lists. A small local web page
served by the bridge (stdlib `http.server`, `http://localhost:6129`, opened from the menu) covers all
of it, needs no GUI toolkit, and works on Windows later. The Push display keeps doing first-run
guidance ("Waiting for Resolume — enable Webserver in Preferences" exists; add "Bar Chaser not
installed — install it from the menu").

The one thing that cannot be automated: Arena's Webserver must be enabled once in Preferences.
Detect it (connection refused on 8080) and show the instruction on the Push and in the menu.

## 6. Tests and CI

- Convert the three scripts to pytest. A fixture starts `mock_resolume.py` on a free port as a
  subprocess and passes host / port to the bridge config; `test_fake_push` becomes several test
  functions sharing one running bridge. Nothing changes in what is tested.
- GitHub Actions: `macos-latest` runs the Python tests and `plugin/build.sh` (the GL host test needs a
  GPU context; mark it skippable on CI if it fails there), `ubuntu-latest` runs the pure tests with
  a real Python 3.9 interpreter plus pyflakes, so the 3.9 constraint is enforced by CI instead of by
  memory.
- Add the pure tests from S13 and the B1 reproduction.

## 7. Docs

`CLAUDE.md` (281 lines) doubles as the handover and duplicates README's control tables; keep it, but
move the "Control map" and "Modes" detail into `docs/` and link. `docs/plans/2026-09-24-step-sequencer.md`
(2,141 lines) is done work; archive or delete, git keeps it.
