# Behavior Director Architecture

This document captures the design of the autonomous behavior system for the
Live2D pet so the body/brain split stays clear. The character is Evelyn; the
`characters/marin/` directory name is a retained asset path.

## The split

- **`src/behavior_state.py`** — the *brain*: a pure, internal-state model
  (valence / arousal / engagement) with no GL/Qt/clock. It does NOT touch the
  model. All time flows through `BehaviorState.update(dt)`.
- **The behavior director (Phase 2b)** — the *body*: reads the state outputs and
  writes model parameters. It owns per-parameter priorities, blinks, gestures and
  speaking behaviour on the Live2D model.

```
 mood tags / F9 / click-drag / mic(recording) / speaking
        │  (explicit event inputs)
        ▼
  BehaviorState   (pure axes, half-life decay)
        │  (state_factor, blink_rate, sway_*, breathing_depth,
        │   fidget_rate, gaze, valence_motion)
        ▼
  behavior director   (per-parameter ownership, motion budget)
        │  (SetSaveParamById only — never physics params)
        ▼
  Live2D model (breath, blink, mouth, head, eyes, expression switches)
```

## `valence_motion` — single definition

`valence_motion(v) = clamp01(0.5 + 0.5 * valence)`

- happy (`valence ≈ +1`) → `1.0`
- neutral (`valence ≈ 0`)  → `0.5`
- sad   (`valence ≈ -1`)  → `0.0`

It is the shared scalar that *suppresses* motion when the model is sad or low
arousal. It is defined once in `BehaviorState.valence_motion()` and used by
`state_factor()`, `sway_amplitude()`, `sway_speed()`, `fidget_rate()` and
`blink_rate()`. Tests and the director use the same method — there is no second
copy.

## `state_factor` and the motion budget

```
final_motion = liveliness x state_factor   (capped by the director's budget)
state_factor = max( state_floor,
                    (0.6 + 0.4*valence_motion)
                  * (0.5 + 0.5*arousal)
                  * (0.4 + 0.6*engagement) )
```

- `state_floor` (default `0.35`) guarantees the pet never **freezes** down to
  nothing when alone, even at zero arousal / very negative valence.
- The engagement term mostly changes **what kind** of behaviour happens (see gaze
  rule) rather than only scaling the amount — so a high-engagement pet looks at
  you, a low-engagement/idle-long one wanders.

## Decay (half-lives, exponential toward baseline)

Config `half_lives`: `valence 240 s`, `arousal 90 s`, `engagement 120 s`.
`z' = z + (target − z) * (1 − 2^(−dt/half_life))` — an exact exponential, so the
raw axes and `state_factor` are **independent of step size** (10 × 0.1 s ≡
1 × 1.0 s).

Axes decay back to:
- valence → closeness-based baseline (`_val_base`, ~0),
- arousal → `0.5` (neutral),
- engagement → a condition-dependent target: `1.0` while listening/speaking,
  else the idle / ignored / idle-long floor.

Event inputs are *impulses*: a mood tag applies fully when the mood **changes**,
with diminishing returns on repeats (idle = 0). No transient mood is persisted.

## Gaze rule (approved)

Gaze is driven by `BehaviorState.gaze()` and enacted by the director:

- **While speaking** — hold gaze **toward the viewer** (straight ahead) with
  small saccades; light nods on sentence starts. Cursor tracking wins when the
  cursor is near the pet and moving.
- **While listening** (user speaking — recording state from `core/recording.py`)
  — attentive posture and gaze toward the viewer.
- **Ignored / idle-long** — wandering gaze away from the viewer; the longer the
  idle, the more the gaze wanders (higher intensity at `idle_long_after_s`).

## Director rules (Phase 2b)

- Never write physics parameters (hair/ear/tail/cloth). Only `Ear_smile` is a
  direct ear parameter. Ear/tail life comes from head motion.
- Writes go through the procedural layer using `SetSaveParamById` only (because
  `Update()` starts with `LoadParameters()`).
- Closed blink reaches exactly `0.0`; a wink suppresses blink only for its eye.
- A gesture owns only the parameters it uses; breathing/sway continue on the rest.
- Config `behavior_director true/false` falls back to the old idle behaviour.

## Director integration (how 2b lands in the widget)

`src/live2d_face.py` owns a `BehaviorDirector` (pure, `src/behavior_director.py`)
and holds the shared `BehaviorState`. When `behavior_director` is on and a state
is present, `paintGL` replaces the legacy `_tick_idle` / `_tick_idle_behaviour` /
`_tick_blink` with:

1. Feed the active wink screen-side into `director.set_wink_side(...)` (so the
   blink is suppressed only for the winked eye).
2. `behavior_state.update(dt)` + `behavior_state.set_speaking(self._speaking)`
   (speaking comes from the speaker playback each frame).
3. `frame = director.update(dt, state)` then `_apply_director_frame(frame)` which
   folds the frame into the procedural layer:
   - `frame.additive`  → ADDED on top of the mood base (head/eye/body drift).
   - `frame.override`  → SET (breathing).
   - `frame.blink`     → the eye-openness override; `None` = that eye is owned
     elsewhere (the winked eye), so the wink gesture wins.
4. `_tick_mouth`, `_tick_cursor` and `_tick_body` (fx/look/gesture/wink) then
   layer on top, so a gesture that writes an absolute value for a param it owns
   overrides the director's additive drift for that param (per-parameter
   ownership); `_flush_params` pushes everything via `SetSaveParamById`.

### Per-parameter priority (mood ≤ idle drift ≤ cursor < gesture)

The director handles idle drift + gaze; cursor (`_tick_cursor`) adds attention
head/eye offsets; gestures/fx (`_tick_body`) run last and override only the
params they own. Breathing (override) and the blink reflex are independent of
liveliness so the pet never looks statue-like at the "Still" preset.

### Structural physics safety

The director may emit **only** params in `DIRECTOR_PARAMS`
(`ParamAngleX/Y/Z`, `ParamEyeBallX/Y`, `ParamEyeLOpen/ROpen`, `ParamBreath`,
`ParamBodyAngleX0/Y0/Z0`, `ParamChestAngleY`). The lean param is configurable
(`lean_param`) among the body-rotation candidates. `PHYSICS_PARAMS` enumerates
the hair/ear/tail/cloth params `简.physics3.json` produces; the two sets are
disjoint and a test asserts no physics param ever leaks into a frame.

### Motion budget & liveliness (config `motion_budget`, `liveliness`)

- `final_motion = liveliness x state_factor`, capped by the budget numbers
  (`max_head_angle 8.0°`, `max_eye_offset 0.5`, `max_body_drift 2.0°` — subtle by
  default).
- `liveliness` is a plain 0..1 scalar (default `0.7`).

### 2c — Speaking / listening behaviour

- **Speaking** — nods are E V E N T S, not a walker. At each spoken sentence's
  audio start (`on_body` → `face.note_speech_start(text)`), the GUI fires a
  small head nod (`director.nod(amp)`) on `ParamAngleY`. Amplitude is scaled by
  arousal and liveliness (excited nods more, sad/calm less) and bumped by
  emphasis punctuation (`!`/`?`); it is capped by the head budget. A nod is
  dropped if one is already running or within `NOD_MIN_GAP` (1.5 s).
- **Listening** — the director autonomously fires a small slow nod every 3–7 s
  (`_next_listen_nod`) and eases a lean onto the configured `lean_param`
  (default `ParamBodyAngleY0`; `lean_sign` flips the direction). Both stay
  under `max_body_drift`. The lean param/sign are config values so the user can
  find which body param actually looks like leaning in; the preview tool lets
  you try `ParamBodyAngleY0`, `ParamChestAngleY`, `ParamBodyAngleX0` and
  `ParamBodyAngleZ0` live, and the chosen value is stored in config.

### 2d — Organic motion

- **Min blink gap**: `BlinkController.MIN_GAP` (0.4 s) — double blinks and any
  rapid retriggers can never flutter closer than 0.4 s; a wink still suppresses
  blink only for its eye, and a closed blink reaches exactly `0.0`.
- **Breath pause**: the breath override occasionally holds at rest for a short
  window. Config `breath_pause_enabled` (default true, easy to disable),
  `breath_pause_every` `[40, 90]` s between pauses and `breath_pause_hold`
  `[1.0, 1.5]` s hold duration. An optional slow sigh (`_sigh_peak`) plays right
  after a long spoken reply when arousal is low.
- Sway/breath stay eased-noise walkers (not sine sums); double blink retained.

### 2e — Liveliness presets & behavior-director toggle (right-click menu)

- **Liveliness** submenu with the exact-value presets Still `0.0` / Calm `0.4` /
  Normal `0.7` / Lively `1.0` (checkable, current shown). Selections are
  persisted to `pet_state.json` (`liveliness`) and reloaded at startup; the
  saved value is restored even when the saved window position is off-screen
  (same fix as the idle toggle). The preview tool has the same presets as
  buttons plus a fine slider and the exact value in the status bar.
- **Behavior director: on/off** checkable action next to it, persisted the same
  way (`behavior_director` in `pet_state.json`); toggling calls
  `face.set_behavior_director()` which swaps between the director and the legacy
  idle path. Config `behavior_director` remains the fallback default.

### Input wiring (app.py)

- Mood tags → `behavior_state.mood_impulse(name)` at audio start (`on_body`).
- F9 hotkey wrapper → `behavior_state.hotkey()`.
- Left-click/press → `behavior_state.interact()`.
- Listening (user speaking) → `bus.listening True/False` around
  `record_until_silence` in `_run_turn` → `Pet.on_listening` →
  `set_listening()`. No always-on mic monitor; stays neutral when unknown.
- Speaking (pet) → the widget sets it each frame from speaker playback; sentence
  starts also trigger `note_speech_start` (nod) from `on_body`.
- The preview tool (`tools/live2d_preview.py`) drives the same state with
  valence / arousal / engagement / liveliness sliders for visual tuning.
