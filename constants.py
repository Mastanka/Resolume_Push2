"""Control names (push2-python's button and encoder names), menus, palette slots and tuning
constants shared by the bridge and its screen modules."""

from __future__ import annotations

TRACK_ENCODERS = [f"Track{i} Encoder" for i in range(1, 9)]
MASTER_ENCODER = "Master Encoder"
NAV_BUTTONS = ["Up", "Down", "Left", "Right", "Page Left", "Page Right"]
MIX_BUTTON = "Mix"                          # B_3 on the control map
PLAY_BUTTON = "Play"                        # B_1: hold + pad = launch clip
STOP_BUTTON = "Record"                      # B_2: hold + pad = stop layer
MOVE_BUTTON = "Convert"                     # B_4: hold + touch knob = move param
TAP_BUTTON = "Tap Tempo"                    # B_5
TEMPO_ENCODER = "Tempo Encoder"             # K10
BLACKOUT_BUTTON = "Stop"                    # "Stop Clip": composition master 0 / back
MUTE_BUTTON = "Mute"                        # hold + pad = mute (bypass) that layer
SOLO_BUTTON = "Solo"                        # hold + pad = solo that layer
SCENE_BUTTONS = ["1/32t", "1/32", "1/16t", "1/16", "1/8t", "1/8", "1/4t", "1/4"]  # = pad rows, top first
BLINK = 0.25          # s on / off for blinking LEDs
BLACKOUT_RESEND = 0.5  # s: blackout / restore not reported back by Resolume yet → send it again
BLACKOUT_TRIES = 10    # resends before giving up (with a message on the display)
METRONOME_BUTTON = "Metronome"              # pad pulse on / off
TAP_PATH = "tempocontroller/tempo_tap"
RESYNC_PATH = "tempocontroller/resync"
BEAT_ON = 0.12        # s a beat stays lit (Tap Tempo button, pad pulse)
LED_HZ = 50           # LED updates per second (beat edges); the display runs at display_fps
MASTER_COLOR_BUTTON = "Master"              # COLOR on the composition's colour effect
PASTE_BUTTON = "Duplicate"                  # COLOR: hold + pad / row / column = paste colour
NOTE_TIME = 1.8       # s a short message stays on the display
NOTE_BUTTON, SESSION_BUTTON = "Note", "Session"   # the two views: sequencer / clips
BROWSE_BUTTON, REPEAT_BUTTON, ACCENT_BUTTON = "Browse", "Repeat", "Accent"
DELETE_BUTTON, DOUBLE_LOOP_BUTTON, FIXED_LENGTH_BUTTON = "Delete", "Double Loop", "Fixed Length"
OCTAVE_UP, OCTAVE_DOWN = "Octave Up", "Octave Down"
SELECT_BUTTON = "Select"                    # SEQ: tap = multi-select on / off; hold + group button = store
LAYOUT_BUTTON, SCALE_BUTTON = "Layout", "Scale"   # SEQ: buttons right of the pads = pad groups / grid
SWING_ENCODER = "Swing Encoder"
# Buttons whose "held" state other controls read. Kept by the physical layer at the top of
# Bridge.button() for every press and release, whatever screen is showing.
HELD_FLAGS = {"Shift": "shift", PLAY_BUTTON: "play_held", STOP_BUTTON: "stop_held", PASTE_BUTTON: "paste_held",
              MUTE_BUTTON: "mute_held", SOLO_BUTTON: "solo_held", MOVE_BUTTON: "convert_held",
              DELETE_BUTTON: "delete_held", BROWSE_BUTTON: "browse_held", FIXED_LENGTH_BUTTON: "fixed_len_held",
              SELECT_BUTTON: "select_held"}
SEQ_HZ = 100          # sequencer clock ticks per second
# Menus on the buttons above the display, per view. CLIP view (Session): white; SEQUENCER view (Note): red.
CLIP_MENUS = {"Upper Row 1": "clip_params", "Upper Row 2": "color", "Upper Row 3": "clip_fx",
              "Upper Row 6": "layer_params", "Upper Row 7": "layer_fx"}
SEQ_MENUS = {"Upper Row 1": "seq_env", "Upper Row 2": "seq_settings", "Upper Row 3": "seq_presets",
             "Upper Row 4": "seq_mapping"}
SEQ_PAGES = {"seq_env": ["Attack", "Decay", "Sustain", "Release", "Gate"],     # knobs per SEQ menu
             "seq_settings": ["Direction", "Length", "Level"], "seq_presets": [], "seq_mapping": []}
MAP_FIXTURES = 32     # MAPPING: fixtures on the top four pad rows (Octave ▲ ▼ = next 32)
MAP_BLINK = 0.08      # s per phase of the double blink that confirms a stored mapping
PRESET_BANK = "techno"   # banks.BANKS: the presets on the buttons below the display (Shift = 9-16)
CONFIRM_NO, CONFIRM_YES = 6, 7   # PRESETS questions: buttons 7 (NO) and 8 (YES) below the display
PARAM_MODES = ("clip_params", "layer_params")
FX_MODES = ("clip_fx", "layer_fx")
MIX_MODES = ("mix", "mute", "solo")       # screens on top of either view (Mix / Mute / Solo button)
OVERLAY_BUTTONS = {MIX_BUTTON: "mix", MUTE_BUTTON: "mute", SOLO_BUTTON: "solo"}
HOLD_TIME = 0.4       # s: shorter press = click (the screen stays), longer = hold (back on release)
SEQ_RED, SEQ_RED_DIM = "L6", "L6_dim"     # layer colour 7 is red: the SEQUENCER menu buttons
FX_SOURCES = [("Clip", "clip"), ("Layer", "layer")]   # FX menus: CLIP EFFECTS / LAYER EFFECTS
UPPER_ROW = [f"Upper Row {i}" for i in range(1, 9)]
LOWER_ROW = [f"Lower Row {i}" for i in range(1, 9)]
TEMPO_PATH = "tempocontroller/tempo"
TAP_RESET = 2.0       # s without a tap starts a new tap count
TAP_FLASH = 0.1       # s the Tap Tempo button stays lit per tap
DIM = 0.15            # brightness of loaded-but-not-playing pads
MID = 0.45            # brightness of playing pads between beats (pulse)
PALETTE_BASE = 64     # Push palette slots 64..79 are overwritten with the layer colours
SWATCH_BASE = 80      # slots 80..87 = the COLOR menu's palette swatches on Lower Row 1-8
# SEQ pad sections, top to bottom: steps red, pattern row orange, pads yellow. Four shades of each
# hue (S_red, S_red_mid, S_red_dim, S_red_off, …) in palette slots 96..107.
SECTION_BASE = 96
SECTION_RGB = [("red", (255, 40, 40)), ("orange", (255, 110, 0)), ("yellow", (255, 210, 0))]
SECTION_SHADES = [("", 1.0), ("_mid", MID), ("_dim", DIM), ("_off", 0.06)]
COLOR_SOURCES = [("clip", "video"), ("layer", "video/effects")]   # where colour params are searched
# COLOR menu knobs: (label, kind, coarse step, fine step). kind r/g/b 0..255, h 0..360, s/v 0..100
COLOR_KNOBS = [("Red", "r", 3, 1), ("Green", "g", 3, 1), ("Blue", "b", 3, 1),
               ("Hue", "h", 2, 0.5), ("Saturation", "s", 1, 0.2), ("Brightness", "v", 1, 0.2)]

AUTO_SKIP_LAST = {"bypassed", "position", "duration"}
AUTO_SOURCES = [                     # where "auto" layers look for parameters, in order
    ("layer", "video/opacity"),
    ("clip", "video/sourceparams"),
    ("clip", "video/effects"),
    ("layer", "video/effects"),
    ("clip", "transport"),
]
OVERRIDE_HOLD = 0.8   # s to trust our own sent value over polled state (prevents jitter)
