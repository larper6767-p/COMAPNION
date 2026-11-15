"""Phase 2a report helper: print the behavior-state outputs table (req 6a).

Builds representative BehaviorState scenarios and prints every output
(blink_rate, sway_amplitude, sway_speed, breathing_depth, fidget_rate,
state_factor, gaze) so the numbers can be pasted into the Phase 2a report.

Run:
    python tools/behavior_state_table.py
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from behavior_state import BehaviorState  # noqa: E402

CLOSENESS = 80.0


def drive(scenario: str) -> BehaviorState:
    """Build a representative state for a named scenario (fresh, just settled)."""
    s = BehaviorState(rng=random.Random(11), closeness=CLOSENESS)
    if scenario == "neutral":
        settle = 5
    elif scenario == "sad_low":
        s.mood_impulse("sad")
        settle = 5
    elif scenario == "excited":
        s.mood_impulse("joyful")
        s.mood_impulse("excited")
        settle = 5
    elif scenario == "ignored":
        settle = 400                     # ~400 s -> past ignored_after_s (300)
    elif scenario == "idle_long":
        settle = 1000                    # >= idle_long_after_s (900)
    elif scenario == "listening":
        s.set_listening(True)
        settle = 300                     # raise engagement toward attentive
    elif scenario == "speaking":
        s.set_speaking(True)
        settle = 300                     # raise engagement toward attentive
    else:
        settle = 5
    for _ in range(settle):
        s.update(1.0)
    return s


def main() -> None:
    scenarios = ["neutral", "sad_low", "excited", "ignored",
                 "idle_long", "listening", "speaking"]
    headers = ["scenario", "val", "aro", "eng", "st_fac",
               "blink", "sway_amp", "sway_spd", "breath", "fidget", "gaze"]
    print(" | ".join(h.ljust(10) for h in headers))
    print("-+-".join("-" * 10 for _ in headers))
    for name in scenarios:
        s = drive(name)
        g = s.gaze()
        row = [
            name.ljust(10),
            f"{s.valence:+.2f}".ljust(10),
            f"{s.arousal:.2f}".ljust(10),
            f"{s.engagement:.2f}".ljust(10),
            f"{s.state_factor():.2f}".ljust(10),
            f"{s.blink_rate():.2f}".ljust(10),
            f"{s.sway_amplitude():.2f}".ljust(10),
            f"{s.sway_speed():.2f}".ljust(10),
            f"{s.breathing_depth():.2f}".ljust(10),
            f"{s.fidget_rate():.2f}".ljust(10),
            f"{g.mode}/{g.intensity:.1f}/viewer={int(g.toward_viewer)}".ljust(10),
        ]
        print(" | ".join(row))

    # Neutral with the user's liveliness=0.7 presets -> final_motion
    print()
    for liv in (0.0, 0.4, 0.7, 1.0):
        n = drive("neutral")
        print(f"neutral liveliness={liv}: final_motion = {liv * n.state_factor():.2f}")


if __name__ == "__main__":
    main()
