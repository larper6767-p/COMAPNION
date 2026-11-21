# Prompt budget and observability

Phase 1 makes prompt size visible and optionally bounded. It does **not** edit
`config/personality.json` or any persona text, and it does not log prompt text.

Every measured conversation turn writes two privacy-safe lines to
`logs/evelyn.log`:

```text
[prompt] total=6341 budget=12000 pct=53 sys=4521 hist=1820 msgs=30/30 all=40 trimmed=0 image=no sections=persona:3643,aliveness:350,memory:528
[prompt] usage prompt=6120 completion=87 finish=stop est=6341 ratio=0.97
```

The first line is the pre-request estimate by prompt section. The second is the
provider's actual usage when the provider returns it. Both contain counts only.

## Configuration

`config/prompt_budget.yaml` owns the knobs:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master switch for reports and logging |
| `max_prompt_tokens` | `12000` | Hard cap for Cerebras/cloud turns; `0` = observe only |
| `ollama_max_prompt_tokens` | `1600` | Lower cap for the local 2048-context Ollama path |
| `warn_ratio` | `0.85` | Log at WARNING at this fraction of the active cap |
| `min_history_messages` | `2` | Never trim below this many newest messages |
| `log_every_turn` | `true` | Log every turn; `false` logs only warnings/trims |

The estimate uses four characters per token plus a small per-message
overhead. It is deliberately conservative and dependency-free. The provider
usage line reports `ratio=actual/estimated`, so a long-running calibration can
be checked without sending prompt text anywhere.

## Budget behavior

1. The system prompt (persona, feature-owned blocks, body/clock/agenda/state,
   web context, question guard) is assembled exactly as before.
2. The newest `max_history` history messages are selected.
3. If the estimated total exceeds the active cap, the **oldest** selected
   messages are removed from the outgoing copy only.
4. At least `min_history_messages` newest messages remain, and the latest user
   turn is never dropped.
5. The in-memory history is untouched. Episode logging, daily summaries, and
   the next turn still see the full conversation.
6. If the system prompt alone exceeds the cap, the system text is still sent;
   `/stats` and the log show `system-over-budget`.

There is no hard cap for local Ollama when `ollama_max_prompt_tokens` is set to
`0`, but the default 1600 leaves room for the configured completion inside the
fixed 2048-token context.

## Where to see it

- **CLI:** `/stats` prints the last estimate, section sizes, history trim
  count, and provider usage. `logs/evelyn.log` carries the same data per turn.
- **GUI:** right-click → **Stats** shows the same last-turn prompt report under
  the token counters.
- **Aggregate:** `tools/measure_prompt.py` reads current and rotated logs and
  reports averages, trims, over/near-budget turns, and provider finish reasons.

```powershell
.\.venv\Scripts\python.exe tools\measure_prompt.py
.\.venv\Scripts\python.exe tools\measure_prompt.py --days 7
.\.venv\Scripts\python.exe tools\measure_prompt.py --since "2026-11-21 22:00:00"
.\.venv\Scripts\python.exe tools\measure_prompt.py --json
```

## Manual checklist

- [ ] Run the app and send one typed or spoken message.
- [ ] Open `logs/evelyn.log` and confirm one `[prompt] total=...` line and,
      when the provider returns usage, one `[prompt] usage ...` line.
- [ ] Confirm the log lines contain counts/section names but no persona text.
- [ ] Open `/stats` (CLI) or right-click → Stats (GUI) and confirm the last
      prompt estimate, section sizes, and actual usage match the log.
- [ ] Temporarily set `max_prompt_tokens: 1`, send a few turns, and confirm
      only the oldest outgoing history is trimmed while the newest turn is
      still answered; then restore `12000`.
- [ ] Temporarily set `max_prompt_tokens: 0` and confirm `budget=off` and no
      trimming.
- [ ] Run `.\.venv\Scripts\python.exe tools\measure_prompt.py` and confirm the
      aggregate summary appears.
- [ ] Confirm the bubble still sits left of the model with a 2 px gap, is
      narrower and semitransparent, and that opening Stats does not move or
      resize the model.

No persona text or closeness/relationship state is changed by this phase.
