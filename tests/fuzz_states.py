"""State fuzzer for Bridge: a random walk of physically consistent press / release / turn / pad /
composition events against the real Bridge (no Push, no Resolume, no mock), with invariants checked
after every event. Reports, does not assert: exit code 0. Every run is reproducible by its seed.

    python tests/fuzz_states.py            # 16 seeds x 2500 events, ~30 s
    python tests/fuzz_states.py 4 500      # quicker

Invariants: no exception in a handler or in button_colors / pad_colors / snapshot / render; a
*_held flag is only True while its button is physically down; pressed / col_pressed / flash /
held_steps are empty when nothing is held; screen sub-state (preset_pick, confirm, map_armed,
move_src) only exists in its screen; sel and the grid offsets point inside the composition."""
import sys, random, tempfile, pathlib, traceback, itertools, collections, time as _time
ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
import push_resolume_bridge as B
import display

CLOCK = [1000.0]
_time.time = lambda: CLOCK[0]                     # the bridge's time.time() = a virtual clock

class RestStub:
    url = "stub://"
    def composition(self, *a, **k): raise OSError("stub: no Resolume")
    def __getattr__(self, name):                       # every REST call succeeds and does nothing
        return lambda *a, **k: 204

# ---- synthetic composition -------------------------------------------------------------- #
SLICES = ["—", "Bar A / 1 - 423 141 RGB", "Bar A / 424 - 846 141 RGB 2", "Bar B / 1 - 855 h3 2m grb",
          "Bar C / 1 - 423 141 RGB", "Bar D / 1 - 855 h3 2m grb", "Bar E / 1 - 423 141 RGB",
          "Bar A", "Bar B", "Bar C", "Bar D", "Bar E"]

def make_comp(rng, n_layers, n_cols, chaser_tracks, master_id=None):
    ids = itertools.count(rng.randrange(1, 10 ** 6) * 100)
    def rg(v, lo=0.0, hi=1.0): return {"id": next(ids), "valuetype": "ParamRange", "value": v, "min": lo, "max": hi}
    def bo(v): return {"id": next(ids), "valuetype": "ParamBoolean", "value": v}
    def ch(v, opts): return {"id": next(ids), "valuetype": "ParamChoice", "value": v, "options": opts, "index": opts.index(v)}
    def st(v): return {"id": next(ids), "valuetype": "ParamState", "value": v}
    def sg(v): return {"id": next(ids), "valuetype": "ParamString", "value": v}
    def co(v): return {"id": next(ids), "valuetype": "ParamColor", "value": v, "palette": ["#ff0000ff", "#00ff00ff"]}
    def fx(name, extra=None):
        params = {"Opacity": rg(1.0), "Scale": rg(0.5), "Color": co("#ff8800ff")}
        params.update(extra or {})
        return {"id": next(ids), "name": name, "display_name": {"value": name}, "bypassed": bo(False), "params": params}
    def chaser(track):
        params = {"Preset": sg("mock_rig"), "Reload": {"id": next(ids), "valuetype": "ParamEvent"},
                  "Track": ch(str(track), ["1", "2", "3", "4"]), "Master": rg(1.0), "Edge": rg(0.0, 0, 20),
                  "Outside": ch("Transparent", ["Transparent", "Black", "Pass through"]),
                  "Mode": ch("Texture", ["Texture", "Solid", "Show pads"])}
        for k in range(24):
            params[f"Pad {k + 1}"] = ch(SLICES[k + 1] if k < 6 else SLICES[0], SLICES)
        for k in range(24):
            params[f"Level {k + 1}"] = rg(0.0)
        return {"id": next(ids), "name": "Bar Chaser", "display_name": {"value": "Bar Chaser"}, "bypassed": bo(False), "params": params}
    def clip(l, c):
        if rng.random() < 0.25:
            return {"name": {"value": ""}, "connected": st("Empty"), "selected": bo(False), "video": None, "transport": None}
        return {"name": {"value": f"clip {l}.{c}"}, "connected": st(rng.choice(["Disconnected", "Connected", "Disconnected"])),
                "selected": bo(False),
                "video": {"sourceparams": {"Frequency": rg(0.3), "Width": rg(0.5), "Kind": ch("A", ["A", "B", "C"]), "Loop": bo(True)},
                          "effects": [fx("Transform")] if rng.random() < 0.6 else []},
                "transport": {"controls": {"speed": rg(1.0, 0, 10), "position": rg(0.0), "duration": rg(5.0, 0, 100)}}}
    layers = []
    for l in range(1, n_layers + 1):
        effects = [fx("Transform")] if rng.random() < 0.5 else []
        if l in chaser_tracks:
            effects.append(chaser(chaser_tracks[l]))
        layers.append({"name": {"value": f"L{l}"}, "master": rg(0.8), "bypassed": bo(False), "solo": bo(False),
                       "video": {"opacity": rg(1.0), "effects": effects},
                       "clips": [clip(l, c) for c in range(1, n_cols + 1)]})
    master = rg(1.0)
    if master_id is not None:
        master["id"] = master_id
    return {"master": master,
            "tempocontroller": {"tempo": rg(120.0, 20, 500), "tempo_tap": {"id": next(ids), "valuetype": "ParamEvent"},
                                "resync": {"id": next(ids), "valuetype": "ParamEvent"}},
            "columns": [{"connected": st("Disconnected")} for _ in range(n_cols)],
            "video": {"effects": [fx("Colorize")]},
            "layers": layers}

# ---- controls ----------------------------------------------------------------------------- #
BUTTONS = ["Shift", "Play", "Record", "Mix", "Mute", "Solo", "Convert", "Tap Tempo", "Metronome", "Stop", "Master",
           "Duplicate", "Note", "Session", "Browse", "Repeat", "Accent", "Delete", "Double Loop", "Fixed Length",
           "Octave Up", "Octave Down", "Select", "Layout", "Scale", "Up", "Down", "Left", "Right", "Page Left",
           "Page Right"] + B.UPPER_ROW + B.LOWER_ROW + B.SCENE_BUTTONS
FLAGS = [("shift", "Shift"), ("play_held", "Play"), ("stop_held", "Record"), ("paste_held", "Duplicate"),
         ("mute_held", "Mute"), ("solo_held", "Solo"), ("convert_held", "Convert"), ("delete_held", "Delete"),
         ("browse_held", "Browse"), ("fixed_len_held", "Fixed Length"), ("select_held", "Select")]

class Phys:
    def __init__(self):
        self.buttons, self.pads, self.touched = set(), set(), set()

def new_bridge():
    cfg = B.load_config(ROOT + "/config.yaml")
    d = pathlib.Path(tempfile.mkdtemp())
    for k in ("pins_file", "colors_file", "chases_file"):
        cfg[k] = str(d / f"{k}.yaml")
    cfg["sequencer"]["preset_folder"] = ROOT + "/tests/fixtures/rig"
    return B.Bridge(cfg, RestStub())

def check(br, phys, problems, trace):
    def bad(kind, detail=""):
        if kind not in problems:
            problems[kind] = (detail, list(trace))
        problems.setdefault("_count", collections.Counter())[kind] += 1
    br.button_colors(); br.pad_colors(); snap = br.snapshot()
    if len(trace) % 20 == 0:
        display.render(snap)
    assert br.overlay in (None, "mix", "mute", "solo")
    if (br.preset_pick is not None or br.confirm is not None) and not br.presets_menu():
        bad("preset state outside PRESETS", f"pick={br.preset_pick} confirm={br.confirm} mode={br.mode}")
    if br.map_armed is not None and not br.mapping():
        bad("map_armed outside MAPPING", f"mode={br.mode}")
    if br.move_src is not None and br.mode not in B.PARAM_MODES:
        bad("move_src outside PARAMS", f"mode={br.mode}")
    for flag, btn in FLAGS:
        if getattr(br, flag) and btn not in phys.buttons:
            bad(f"{flag} stuck (button {btn} not held)", f"view={br.view} mode={br.mode}")
    for ov in list(br.ov_press):
        btn = next(b for b, o in B.OVERLAY_BUTTONS.items() if o == ov)
        if btn not in phys.buttons:
            bad("ov_press stuck", f"{ov} without {btn} held")
    if not phys.pads:
        if br.pressed:
            bad("pressed cells stuck (no pad held)", f"pressed={sorted(br.pressed)} view={br.view}")
        if br.held_steps or br.held_bars:
            bad("held_steps / held_bars stuck (no pad held)", f"{br.held_steps} {br.held_bars}")
    if br.col_pressed and not (phys.buttons & set(B.LOWER_ROW)):
        bad("col_pressed stuck (no Lower Row held)", f"{br.col_pressed}")
    if br.flash and not (phys.buttons & set(B.SCENE_BUTTONS)):
        bad("flash stuck (layer master left at 100 %)", f"{br.flash}")
    if br.touched is not None and br.touched not in phys.touched:
        bad("touched stuck", f"{br.touched}")
    L, C = br.sel
    if br.comp and br.layer_json(L) is None:
        bad("sel points at a missing layer", f"sel={br.sel} layers={len(br.layers())}")
    elif br.comp and br.clip_json(L, C) is None:
        bad("sel points at a missing clip", f"sel={br.sel} cols={br.max_cols()}")
    if br.comp and br.layer_offset > max(0, len(br.layers()) - 8):
        bad("layer_offset beyond the composition (grid empty)", f"offset={br.layer_offset} layers={len(br.layers())}")
    if br.comp and br.col_offset > max(0, br.max_cols() - 8):
        bad("col_offset beyond the composition (grid empty)", f"offset={br.col_offset} cols={br.max_cols()}")

def run(seed, n_events, problems, crashes):
    rng = random.Random(seed)
    br = new_bridge()
    phys = Phys()
    comp = make_comp(rng, rng.randrange(2, 12), rng.randrange(4, 16), {1: 1, 2: 2, 3: 3, 4: 4})
    br.set_comp(comp)
    trace = collections.deque(maxlen=14)
    for n in range(n_events):
        r = rng.random()
        try:
            if r < 0.30:                                                   # button press / release
                held = [b for b in BUTTONS if b in phys.buttons]
                if held and rng.random() < 0.5:
                    b = rng.choice(held); phys.buttons.discard(b); ev = ("release", b); br.button(b, False)
                else:
                    b = rng.choice(BUTTONS); phys.buttons.add(b); ev = ("press", b); br.button(b, True)
            elif r < 0.55:                                                 # pad press / release
                if phys.pads and rng.random() < 0.5:
                    ij = rng.choice(sorted(phys.pads)); phys.pads.discard(ij); ev = ("pad up", ij); br.pad_released(ij)
                else:
                    ij = (rng.randrange(8), rng.randrange(8)); phys.pads.add(ij); ev = ("pad down", ij)
                    br.pad_pressed(ij, rng.randrange(1, 128))
            elif r < 0.72:                                                 # encoders
                k = rng.random()
                if k < 0.6:
                    idx, inc = rng.randrange(8), rng.choice([-10, -3, -1, 1, 3, 10]); ev = ("turn", idx, inc); br.turn(idx, inc)
                elif k < 0.7:
                    inc = rng.choice([-5, 5]); ev = ("master turn", inc); br.turn_master(inc)
                elif k < 0.8:
                    inc = rng.choice([-1, 1]); ev = ("tempo turn", inc); br.turn_tempo(inc)
                elif k < 0.85:
                    inc = rng.choice([-1, 1]); ev = ("swing turn", inc); br.turn_swing(inc)
                elif k < 0.93:
                    idx = rng.randrange(8); phys.touched.add(idx); ev = ("touch", idx); br.touch(idx)
                else:
                    if phys.touched:
                        idx = rng.choice(sorted(phys.touched)); phys.touched.discard(idx); ev = ("untouch", idx); br.untouch(idx)
                    else:
                        ev = ("noop",)
            elif r < 0.80:                                                 # time passes, the clock ticks
                dt = rng.choice([0.01, 0.05, 0.3, 0.6, 1.5]); CLOCK[0] += dt; ev = ("wait", dt)
                bt = br.beat_time()
                if bt is not None:
                    with br.lock:
                        for (t, bar), v in br.seq.tick(bt).items():
                            br.engine.set_level(t, bar, v)
            elif r < 0.86:                                                 # composition changes
                k = rng.random()
                if k < 0.4:                                                # same composition, new JSON (a poll)
                    ev = ("comp refresh",)
                    br.set_comp(make_comp(random.Random(seed * 7 + n), len(br.layers()), br.max_cols(), {1: 1, 2: 2, 3: 3, 4: 4},
                                          master_id=br.comp["master"]["id"]))
                elif k < 0.8:                                              # another deck / composition, other size
                    nl, nc = rng.randrange(1, 14), rng.randrange(1, 18); ev = ("comp load", nl, nc)
                    br.set_comp(make_comp(rng, nl, nc, {1: 1, 2: 2} if rng.random() < 0.5 else {}))
                else:                                                      # a live value changes
                    pid = rng.choice(list(br.index)); node = br.index[pid]; ev = ("param update", node["valuetype"])
                    if node["valuetype"] == "ParamRange":
                        br.on_param(pid, rng.uniform(node.get("min", 0), node.get("max", 1)))
                    elif node["valuetype"] == "ParamState":
                        br.on_param(pid, rng.choice(["Connected", "Disconnected"]))
                    elif node["valuetype"] == "ParamBoolean":
                        br.on_param(pid, rng.random() < 0.5)
            elif r < 0.90:                                                 # everything let go
                ev = ("release all",)
                for b in list(phys.buttons):
                    br.button(b, False)
                for ij in list(phys.pads):
                    br.pad_released(ij)
                for idx in list(phys.touched):
                    br.untouch(idx)
                phys.buttons.clear(); phys.pads.clear(); phys.touched.clear()
            else:
                ev = ("noop",)
            trace.append(ev)
            check(br, phys, problems, trace)
        except Exception as e:
            key = f"{type(e).__name__}: {e}"
            tb = traceback.format_exc().strip().splitlines()
            where = next((l.strip() for l in reversed(tb) if 'File "' in l and ROOT in l), "?")
            if key not in crashes:
                crashes[key] = (where, list(trace), seed, n)
            return

if __name__ == "__main__":
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    problems, crashes = {}, {}
    t0 = _time.perf_counter()
    for seed in range(seeds):
        run(seed, n, problems, crashes)
    print(f"{seeds} seeds x {n} events in {_time.perf_counter() - t0:.1f} s")
    print(f"\n=== CRASHES (an exception escaping a handler / the LED path): {len(crashes)}")
    for key, (where, trace, seed, n_) in crashes.items():
        print(f"- {key}\n    at {where}\n    seed {seed} event {n_}, last events: {list(trace)}")
    counts = problems.pop("_count", collections.Counter())
    print(f"\n=== INVARIANT VIOLATIONS: {len(problems)} kinds")
    for kind, (detail, trace) in problems.items():
        print(f"- [{counts[kind]}x] {kind}\n    e.g. {detail}\n    last events: {trace}")
