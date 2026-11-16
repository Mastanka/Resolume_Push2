"""Structural fuzzer: random Push input mixed with random edits in Arena (tests/sim_arena.py), against a
real Bridge. Arena edits: layers added / removed / moved, Bar Chaser added (layer or clip) / removed,
Track and Pad changes, fixtures reloaded (fewer, more, long / short), a broken preset, new compositions
(0-6 layers), master changes, Arena offline. Delivery lags like the real WebSocket: parameter updates
for subscribed ids, the whole composition now and then.

After every event: no exception, and the UI invariants of tests/fuzz_states.py. Every ~150 events a
checkpoint stops the sequencer, lets everything settle and checks what reached Arena: no bar left lit,
every Bar Chaser shows the remembered mapping, no write to an id that no longer exists.

    python tests/fuzz_arena.py            # 12 seeds x 1500 events
    python tests/fuzz_arena.py 4 400      # quicker
Reports, exit code 0; tests/test_scenarios.py runs a short walk as an assertion."""
from __future__ import annotations

import collections
import random
import sys
import time as _time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import fuzz_states as F  # noqa: E402  (UI invariants; it installs its own clock, replaced below)
import sim_arena as S  # noqa: E402

_time.time = lambda: S.CLOCK[0]


class SyncThread:
    """Worker threads (Shift + Note, Browse) run at once, so a seed always replays the same way."""

    def __init__(self, target=None, daemon=None, **kw):
        self.target = target

    def start(self):
        self.target()


def patch_threads():
    import seq_screen
    seq_screen.threading = type("T", (), {"Thread": SyncThread})


PUSH_BUTTONS = F.BUTTONS
DEBUG = []            # set to [None] to collect (arena, bridge, lit) at a failing checkpoint


def push_event(rng, br, phys):
    r = rng.random()
    if r < 0.45:
        held = sorted(phys.buttons)
        if held and rng.random() < 0.5:
            b = rng.choice(held); phys.buttons.discard(b); br.button(b, False); return ("release", b)
        b = rng.choice(PUSH_BUTTONS); phys.buttons.add(b); br.button(b, True); return ("press", b)
    if r < 0.8:
        if phys.pads and rng.random() < 0.5:
            ij = rng.choice(sorted(phys.pads)); phys.pads.discard(ij); br.pad_released(ij); return ("pad up", ij)
        ij = (rng.randrange(8), rng.randrange(8)); phys.pads.add(ij); br.pad_pressed(ij, rng.randrange(1, 128))
        return ("pad down", ij)
    k = rng.random()
    if k < 0.7:
        idx, inc = rng.randrange(8), rng.choice([-10, -3, -1, 1, 3, 10]); br.turn(idx, inc); return ("turn", idx, inc)
    if k < 0.8:
        br.turn_master(rng.choice([-5, 5])); return ("master turn",)
    if k < 0.9:
        idx = rng.randrange(8); phys.touched.add(idx); br.touch(idx); return ("touch", idx)
    if phys.touched:
        idx = rng.choice(sorted(phys.touched)); phys.touched.discard(idx); br.untouch(idx); return ("untouch", idx)
    return ("noop",)


def arena_event(rng, a, br):
    ops = []
    n_layers = len(a.comp["layers"])
    ch = a.chasers()
    ops += [("add layer", 2)] if n_layers < 8 else []
    ops += [("remove layer", 1), ("move layer", 1), ("add chaser", 3), ("add clip chaser", 1)] if n_layers else []
    ops += [("remove chaser", 2), ("set track", 3), ("set pad", 3)] if ch else []
    ops += [("fixtures", 1), ("break preset", 0.4), ("new composition", 0.8), ("master", 1), ("offline", 0.3)]
    names, weights = zip(*ops)
    op = rng.choices(names, weights)[0]
    if op == "add layer":
        a.user_add_layer(rng.randrange(1, n_layers + 2))
    elif op == "remove layer":
        a.user_remove_layer(rng.randrange(1, n_layers + 1))
    elif op == "move layer":
        a.user_move_layer(rng.randrange(1, n_layers + 1), rng.randrange(1, n_layers + 1))
    elif op == "add chaser":
        a.user_add_chaser(rng.randrange(1, n_layers + 1), rng.randrange(1, 5))
    elif op == "add clip chaser":
        L = rng.randrange(1, n_layers + 1)
        C = rng.randrange(1, len(a.comp["layers"][L - 1]["clips"]) + 1) if a.comp["layers"][L - 1]["clips"] else None
        if C:
            a.user_add_chaser(L, rng.randrange(1, 5), clip=C)
    elif op == "remove chaser":
        layers = sorted({L for L, C, _ in ch if C is None})
        if layers:
            a.user_remove_chaser(rng.choice(layers))
    elif op == "set track":
        a.user_set_track(rng.choice(ch)[2], rng.randrange(1, 5))
    elif op == "set pad":
        fx = rng.choice(ch)[2]
        opts = fx["params"]["Pad 1"]["options"]
        a.user_set_pad(fx, rng.randrange(24), rng.choice(opts))
    elif op == "fixtures":
        n = rng.choice([2, 4, 6, 9, 12, 26])
        lengths = rng.choice([[460] * n, [rng.choice([460, 920]) for _ in range(n)]])
        a.user_set_fixtures(S.fixtures(n, start=rng.randrange(1, 4)), lengths)
        a.write_preset(br.preset_folder)
    elif op == "break preset":
        a.user_break_preset()
    elif op == "new composition":
        n = rng.randrange(0, 7)
        chasers = {L: rng.randrange(1, 5) for L in range(1, n + 1) if rng.random() < 0.4}
        a.user_new_composition(n, rng.choice([2, 4, 8, 12]), chasers)
    elif op == "master":
        a.user_set_master(rng.choice([0.0, 0.5, 1.0]))
    elif op == "offline":
        with br.lock:
            br.online = False
    return ("arena", op)


def seq_event(rng, br):
    op = rng.random()
    with br.lock:
        if op < 0.3:
            pat = br.seq.patterns[rng.randrange(16)]
            for _ in range(rng.randrange(1, 12)):
                t, k = rng.randrange(4), rng.randrange(24)
                pat.tracks[t].steps.setdefault(f"pad {k + 1}", {})[rng.randrange(pat.length)] = [rng.random(), None]
            return ("seq", "steps")
        if op < 0.6:
            if br.seq.running:
                br.seq.stop()
            else:
                br.seq.start(br.beat_time() or 0.0)
            return ("seq", "run/stop")
        if op < 0.8:
            t = rng.randrange(4)
            br.seq.trigger(t, f"pad {rng.randrange(1, 25)}", 1.0, rng.choice([None, 0.5]), br.beat_time() or 0.0)
            return ("seq", "hold" if t else "flash")
        br.seq.grid = rng.choice(["1/4", "1/8", "1/16", "1/32"])
        return ("seq", "grid")


def checkpoint(a, br, phys, bad):
    """Stop, let go, settle, then check what reached Arena."""
    br.release_all()
    phys.buttons.clear(); phys.pads.clear(); phys.touched.clear()
    with br.lock:
        br.seq.stop()
        bt = br.beat_time() or 0.0
        for (t, bar), v in list(br.seq.voices.items()):           # let go of held voices, as a pad release does
            if v.gate is None:
                br.seq.release(t, bar, bt)
    a.process(br)
    a.deliver_comp(br)
    n0 = len(a.unknown)
    S.settle(a, br, 4.0, 40)
    if len(a.unknown) > n0:
        bad("write to an id that no longer exists", a.unknown[n0:n0 + 3])
    lit = a.lit()
    if lit:
        bad("bar left lit after everything stopped", lit)
        if DEBUG:
            DEBUG.append((a, br, lit))
            raise RuntimeError("stopped at the first failing checkpoint (DEBUG)")
    want = br.seq.pad_config or []
    for L, C, fx in a.chasers():
        opts = fx["params"]["Pad 1"]["options"]
        if not any(o != S.UN for o in opts):
            continue
        for k, v in enumerate(want):
            if v in opts and fx["params"][f"Pad {k + 1}"]["value"] != v:
                bad("a Bar Chaser does not show the mapping", (L, C, k + 1, v, fx["params"][f"Pad {k + 1}"]["value"]))
                break


def run(seed, n, problems, crashes):
    rng = random.Random(seed)
    patch_threads()
    a = S.Arena(layers=rng.randrange(1, 6), cols=rng.choice([4, 8]),
                chasers={2: 1, 3: 3} if rng.random() < 0.7 else {}, geometry=rng.random() < 0.3)
    br = S.make_bridge(a)
    phys = F.Phys()
    trace = collections.deque(maxlen=16)

    def bad(kind, detail):
        if kind not in problems:
            problems[kind] = (detail, list(trace), seed)
        problems.setdefault("_count", collections.Counter())[kind] += 1

    for step in range(n):
        try:
            r = rng.random()
            if r < 0.5:
                ev = push_event(rng, br, phys)
            elif r < 0.62:
                ev = arena_event(rng, a, br)
            elif r < 0.72:
                a.deliver_params(br); ev = ("deliver params",)
            elif r < 0.78:
                a.deliver_comp(br); ev = ("deliver comp",)
            elif r < 0.84:
                a.process(br); ev = ("network",)
            elif r < 0.95:
                dt = rng.choice([0.01, 0.05, 0.2, 0.6]); S.advance(dt); br.seq_step(); ev = ("tick", dt)
            else:
                ev = seq_event(rng, br)
            trace.append(ev)
            if step % 150 == 149:
                trace.append(("checkpoint",))
                checkpoint(a, br, phys, bad)
            ui = {}
            F.check(br, phys, ui, trace)
            for kind, val in ui.items():
                if kind == "_count" or (not br.layers() and kind.startswith("sel points")):
                    continue
                bad(kind, val[0])
        except Exception as e:
            key = f"{type(e).__name__}: {e}"
            tb = traceback.format_exc().strip().splitlines()
            where = [l.strip() for l in tb if 'File "' in l and str(ROOT) in l][-3:]
            if key not in crashes:
                crashes[key] = (where, list(trace), seed, step)
            return


if __name__ == "__main__":
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
    problems, crashes = {}, {}
    t0 = _time.perf_counter()
    for seed in range(seeds):
        run(seed, n, problems, crashes)
    print(f"{seeds} seeds x {n} events in {_time.perf_counter() - t0:.1f} s")
    print(f"\n=== CRASHES (an exception escaping the bridge): {len(crashes)}")
    for key, (where, trace, seed, step) in crashes.items():
        print(f"- {key}\n    at {where}\n    seed {seed} event {step}, last events: {trace}")
    counts = problems.pop("_count", collections.Counter())
    print(f"\n=== INVARIANT VIOLATIONS: {len(problems)} kinds")
    for kind, (detail, trace, seed) in problems.items():
        print(f"- [{counts[kind]}x] {kind}\n    e.g. {detail}\n    seed {seed}, last events: {trace}")
