"""CLI voice companion — mic → Whisper → Cerebras/Ollama → Fish Audio → speakers.

Usage:
    python src/she.py
    python src/she.py path/to/config/dir

Commands (at the > prompt):
    Enter               record voice
    text + Enter        send text message
    /remember <fact>    save a fact to memory
    /forget             clear all memory facts
    /mute               toggle voice on/off
    /volume <0-1>       set playback volume
    /speed <0.5-2.0>    set TTS speed (1.0 = normal)
    /reload             hot-reload personality.json
    /stats              show token / memory / relationship stats
    /t <seconds>        change max recording duration
    /quit               exit
"""
import os
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# ── Load .env before any core imports that read API keys at import time ───────
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv()  # fallback: default discovery (CWD, home, etc.)
# ─────────────────────────────────────────────────────────────────────────────

import re
import sys
import time
import random
import logging
import datetime
import subprocess
import webbrowser
import threading
from typing import Dict, List, Optional, Tuple

from faster_whisper import WhisperModel
from fishaudio import FishAudio

from core.audio import Speaker
from core.config import Config
from core.memory import MemoryManager
from core.model import ModelInterface, parse_mood_tag, split_sentences, next_fallback
from core.recording import record_until_silence, WakeWordDetector
from core.whisper_cache import load_cached as load_whisper_cached
from memory.vector_store import create_vector_store
logger = logging.getLogger(__name__)


class VoiceCompanion:
    """Main voice companion: ties all core modules together."""

    def __init__(self, config: Config):
        self.config = config
        self.history: List[Dict[str, str]] = []
        self.running = True
        self.max_record_seconds = 20.0
        # Feature 24: sticky long mode for the session (toggled via /long).
        self._long_mode = False

        # ── Memory ────────────────────────────────────────────────────────────
        self.memory = MemoryManager(
            config_dir=config.config_dir,
            tiers=config.closeness_tiers,
            closeness_enabled=config.closeness_enabled,
        )
        raw_facts = config.load_memory()
        self.memory.load_facts(raw_facts)

        # ── Vector memory (Feature 2) ─────────────────────────────────────────
        # Disabled by default in config/memory.yaml; when off this is a no-op and
        # the memory pipeline is byte-identical to before.
        try:
            self.vector_store = create_vector_store()
            if getattr(self.vector_store, "enabled", False):
                self.memory.attach_vector_store(self.vector_store)
                # Feature 2 maintenance: after the collection rename the old
                # facts were never re-embedded, and re-summarising added a new
                # vector per summary text.  Mirror the current sources of truth
                # (memory.json + summaries.json) before the first prompt.
                try:
                    synced = self.memory.sync_vector_store()
                    if synced:
                        logger.info(
                            "Vector memory synced (%d stale/missing item(s) fixed)",
                            synced)
                except Exception as e:
                    logger.warning("Vector memory sync skipped: %s", e)
                logger.info(
                    "Vector memory enabled (%s, collection=%s, %d item(s))",
                    self.vector_store.backend_name,
                    self.vector_store.collection_name,
                    self.vector_store.count(),
                )
        except Exception as e:
            logger.warning(f"Vector memory unavailable: {e}")
            self.vector_store = None

        # ── Whisper ───────────────────────────────────────────────────────────
        model, device, compute = config.resolve_whisper_settings()
        logger.info(f"Loading Whisper ({model})…")
        # Try the local cache first so warm starts do not wait on a HuggingFace
        # revision check.  A cache miss falls back to the normal download.
        self.whisper, _whisper_cached = load_whisper_cached(
            WhisperModel,
            model,
            device=device,
            compute_type=compute,
        )
        logger.info(
            "Whisper ready: %s on %s (%s)",
            model, device, "cache" if _whisper_cached else "downloaded",
        )

        # ── Fish Audio / Speaker ──────────────────────────────────────────────
        logger.info("Initialising Fish Audio client…")
        # Feature 23b: the provider's own read timeout is 240 s — one stalled
        # stream read used to mute her for that long.  Both the HTTP client
        # and the per-sentence watchdog below use the configured cap.
        self._tts_timeout = max(10.0, float(config.tts_synth_timeout_s))
        self.fish_client = FishAudio(timeout=self._tts_timeout)
        logger.info(f"Fish Audio read timeout: {self._tts_timeout:.0f}s")
        self.speaker = Speaker(
            client=self.fish_client,
            voice_id=config.preset_voice_id,
            model=config.tts_model,
            emotion_template=config.emotion_template,
            volume=config.volume,
            speed=config.voice_speed,
            cache_enabled=config.tts_cache_enabled,
            cache_max_size=config.tts_cache_max_size,
            ducking_enabled=config.audio_ducking_enabled,
            ducking_volume=config.audio_ducking_volume,
            synth_timeout_s=config.tts_synth_timeout_s,
        )
        # Feature 8: mood-driven delivery — per-mood speed multipliers from
        # config/mood_delivery.json.  The provider re-reads the file when its
        # mtime changes, so edits apply without a restart; an empty table
        # (enabled:false) means the base voice_speed is used for every mood.
        try:
            from emotion.mood_delivery import get_config as _mood_delivery_cfg
            self.speaker.set_mood_speeds(lambda: _mood_delivery_cfg().speed_table())
        except Exception as e:
            logger.warning("mood delivery unavailable: %s", e)

        # ── LLM ───────────────────────────────────────────────────────────────
        logger.info("Initialising model interface…")
        model_name = config.cerebras_model if config.use_cerebras else config.local_model
        # Feature 4: optional model used for image turns (config/vision.yaml).
        try:
            from vision.screen_capture import load_config as _load_vision_cfg
            _vision_model = _load_vision_cfg().vision_model
        except Exception:
            _vision_model = ""
        self.model = ModelInterface(
            model_name=model_name,
            temperature=config.persona["temperature"],
            use_cerebras=config.use_cerebras,
            max_tokens=config.persona.get("max_tokens", 300),
            repeat_penalty=config.persona.get("repeat_penalty", 1.1),
            stop=config.persona.get("stop", []),
            vision_model=_vision_model,
        )

        # ── Wake word ─────────────────────────────────────────────────────────
        self.wake_detector: Optional[WakeWordDetector] = None
        if config.wake_word_enabled:
            self.wake_detector = WakeWordDetector(
                wake_word=config.wake_word,
                sample_rate=config.sample_rate,
                transcribe_fn=self.transcribe,
            )
            logger.info(f"Wake word enabled: '{config.wake_word}'")

        # ── Follow-up context + fact extraction ────────────────────────────────
        # Stores the most recent (user_text, reply_text). Injected into the system
        # prompt below so she can reference continuity without the whole history.
        self.last_exchange: Optional[tuple] = None
        self._turns_since_fact_scan: int = 0
        self._FACT_SCAN_EVERY = 2   # every N turns, to limit API cost on CLI

        # Feature 27b: same-day restart continuity.
        self._load_today_history()

        logger.info("Initialisation complete")

    def _load_today_history(self) -> int:
        """Feature 27b: same-day restart continuity.

        The rolling conversation window lives in RAM only, so every launch
        used to start with amnesia even though today's transcript was already
        on disk.  This replays the recent tail of TODAY'S episode log into
        ``history`` and seeds ``last_exchange`` from the log's closing pair.

        The replay is capped at the same ceiling ``_trim_history`` enforces
        (``max(max_history, summarize_after * 2)``), so a restored window can
        never trip the in-session summariser on turn one.  Returns the number
        of messages restored (0 = disabled or nothing today).
        """
        try:
            from core import aliveness as _alive
            cfg = _alive.load_config(getattr(
                self.config, "aliveness_file", None))
            keep = int(cfg.get("restart_history") or 0)
        except Exception:
            return 0
        if keep <= 0:
            return 0
        try:
            ceiling = max(int(self.config.max_history),
                          int(self.config.summarize_after) * 2)
            replay = _alive.select_replay(
                self.memory.episode_store.today_messages(), keep, ceiling)
            if replay:
                self.history = replay
                pair = _alive.last_exchange_pair(replay)
                if pair:
                    self.last_exchange = pair
                logger.info(
                    "Restart continuity: replayed %d message(s) from today "
                    "(capped at %d)", len(replay), ceiling)
            return len(replay)
        except Exception as e:
            logger.debug(f"Restart continuity skipped: {e}")
            return 0

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def _harvest_memory_notes(self, reply_text: str) -> int:
        """Feature 29: process her self-written {{remember: ...}} notes.

        Notes are already stripped from display and TTS by the reply pipeline;
        this turns qualifying notes into durable facts.  Gated by
        ``config/aliveness.yaml`` (``self_notes``) with a per-day cap
        (``max_notes_per_day``); the same hygiene filter as auto-extraction
        applies, and duplicates (fact card OR today's earlier notes) win.
        Returns the number of facts actually stored.
        """
        from core import aliveness as _alv
        from core import memory_notes as _mn
        cfg = _alv.load_config(getattr(self.config, "aliveness_file", None))
        if not cfg.get("self_notes"):
            return 0
        raw = _mn.extract_notes(reply_text)
        if not raw:
            return 0

        today = datetime.date.today().isoformat()
        if getattr(self, "_notes_day", None) != today:
            self._notes_day = today
            self._notes_count = 0
        cap = int(cfg.get("max_notes_per_day") or 0)
        allowed = max(0, cap - self._notes_count)
        if not allowed:
            logger.debug("[self-notes] daily cap reached (%d)", cap)
            return 0

        known = {f.casefold() for f in self.memory.get_facts() if f}
        added = 0
        for fact in _mn.filter_notes(raw, existing_keys=known):
            if added >= allowed:
                break
            self.memory.add_fact(fact)
            known.add(fact.casefold())
            added += 1
        if added:
            self._notes_count += added
            self.config.save_memory(self.memory.get_facts())
            logger.info("Self-note: stored %d fact(s) she chose to keep "
                        "(%d/%d today)", added, self._notes_count, cap)
        return added

    def _summarize_session(self) -> None:
        """Write a fresh Evelyn summary when the app closes.

        The old summary trigger only fired after 40 messages in one session,
        which meant short sessions never refreshed the "Recent conversation
        history" block.  Summarizing on shutdown (or the last 30 episode turns
        when the in-memory history is short) makes continuity advances daily.
        """
        try:
            from core import aliveness as _alive
            cfg = _alive.load_config(getattr(
                self.config, "aliveness_file", None))
            if not cfg.get("daily_summary"):
                return
        except Exception:
            return
        try:
            messages = list(getattr(self, "history", []) or [])
            if len(messages) < 4:
                messages = self.memory.episode_store.today_messages()[-30:]
            if len(messages) < 4:
                return
            summary = self.model.summarize(
                messages[-40:], self.config.persona["name"])
            if summary:
                today = datetime.date.today().isoformat()
                self.memory.save_summary(today, summary)
                logger.info("Session summary saved for %s", today)
        except Exception as e:
            logger.debug("Session summary skipped: %s", e)

    def shutdown(self) -> None:
        logger.info("Shutting down…")
        self._summarize_session()
        self.speaker.shutdown(timeout=10.0)
        self.config.save_memory(self.memory.get_facts())
        logger.info("Shutdown complete")

    # ── Hot-reload config push (Part 3-B) ─────────────────────────────────────

    def apply_config(self) -> None:
        """Push hot-reloaded personality values into the live model and speaker.

        Called after config.reload() so temperature, max_tokens, speed, etc.
        take effect immediately without restarting.
        """
        p = self.config.persona
        # Model
        self.model.temperature    = p.get("temperature", self.model.temperature)
        self.model.max_tokens     = p.get("max_tokens", self.model.max_tokens)
        self.model.repeat_penalty = p.get("repeat_penalty", self.model.repeat_penalty)
        new_stop = p.get("stop", [])
        if self.config.use_cerebras and len(new_stop) > 4:
            new_stop = new_stop[:4]
        self.model.stop = new_stop
        # Speaker
        self.speaker.set_speed(self.config.voice_speed)
        self.speaker.set_emotion_template(self.config.emotion_template)
        self.speaker.set_ducking(
            self.config.audio_ducking_enabled,
            self.config.audio_ducking_volume,
        )
        # Feature 23b: TTS timeouts hot-apply too — the watchdog attr is a
        # plain field; the HTTP client is rebuilt only when the cap changes.
        new_tts_timeout = max(10.0, float(self.config.tts_synth_timeout_s))
        self.speaker.synth_timeout_s = float(self.config.tts_synth_timeout_s)
        if new_tts_timeout != float(getattr(self, "_tts_timeout", -1)):
            self._tts_timeout = new_tts_timeout
            try:
                self.fish_client = FishAudio(timeout=self._tts_timeout)
                self.speaker.client = self.fish_client
            except Exception as exc:
                logger.warning(f"Fish client timeout swap failed: {exc}")
        logger.info("apply_config: model + speaker updated from reloaded personality")
        try:
            self.memory.relationship.tiers = dict(self.config.closeness_tiers)
            self.memory.closeness_enabled = bool(self.config.closeness_enabled)
        except Exception as e:
            logger.debug("apply_config: relationship tiers not updated: %s", e)

    # ── Transcription ─────────────────────────────────────────────────────────

    def transcribe(self, audio) -> str:
        try:
            # Part 3-E: use only the character name as initial_prompt.
            # Including recent assistant replies biases Whisper toward her vocabulary
            # and can cause it to hallucinate her words into the user's utterance.
            initial_prompt = self.config.persona["name"]

            segments, info = self.whisper.transcribe(
                audio,
                language=self.config.whisper_language,
                beam_size=self.config.whisper_beam_size,
                vad_filter=self.config.whisper_vad_filter,
                initial_prompt=initial_prompt,
                condition_on_previous_text=self.config.whisper_condition_on_previous,
                # Part 3-E: pass the default fallback schedule instead of 0.0 alone,
                # so compression_ratio and log_prob thresholds can actually fire.
                temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                no_speech_threshold=0.6,
            )
            # Part 3-E: segment .text already carries a leading space; join without sep
            text = "".join(s.text for s in segments).strip()
            logger.debug(f"Transcribed: {text[:60]!r}")
            return text
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return ""

    # ── Voice capture (Features 21+22: patient turn-taking + backchannels) ────

    def _record_plain(self):
        """The ORIGINAL capture: fixed silence threshold, no judge."""
        return record_until_silence(
            sample_rate=self.config.sample_rate,
            max_seconds=self.max_record_seconds,
            threshold_multiplier=self.config.vad_threshold_multiplier,
        )

    def _record_voice(self):
        """One voice capture, Features 21+22 style (or the old behaviour).

        With `config/listening.yaml` enabled, the recorder calls this
        judge every ~0.6 s of pause with (audio so far, silence seconds);
        the judge transcribes the RECENT tail (cheap Whisper call), lets
        the endpoint policy decide "still talking" vs "done", and — mid
        long monologue, in the short silence window — fires one small
        listening-noise through the speakers (the recorder drops the
        echoed mic tail right after, so her "Mhm…" never pollutes the
        transcript).

        Disabled or on any failure: `_record_plain()` — byte-for-byte the
        original capture.
        """
        try:
            from core import turn_taking as _tt
            cfg = _tt.get_config()
        except Exception:
            return self._record_plain()
        if not cfg.get("enabled", False):
            return self._record_plain()

        sr = int(self.config.sample_rate)
        try:
            tail_window = float(cfg.get("tail_window_s", 12.0))
        except Exception:
            tail_window = 12.0
        bc_state = {"last": None}

        def judge(audio_sofar, silence_seconds: float) -> dict:
            tail = _tt.tail_seconds(audio_sofar, tail_window, sr)
            try:
                partial = self.transcribe(tail)
            except Exception:
                partial = ""
            dispatch, _why = _tt.decide(partial, silence_seconds, cfg)

            line = None
            if not dispatch:
                try:
                    line = _tt.pick_line(
                        speech_s=(len(audio_sofar) / sr
                                  if audio_sofar is not None else 0.0),
                        sil_s=silence_seconds,
                        now_s=time.monotonic(),
                        last_bc_s=bc_state.get("last"),
                        cfg=cfg,
                    )
                except Exception:
                    line = None
            if line:
                bc_state["last"] = time.monotonic()
                logger.info(f"[backchannel] {line!r}")
                try:
                    self.speaker.say(line)
                    for _ in range(60):            # at most ~3.0 s
                        if not self.speaker.is_playing():
                            break
                        time.sleep(0.05)
                except Exception:
                    pass

            return {"dispatch": bool(dispatch),
                    "mute_s": float(cfg.get("mute_s", 0.7)) if line else 0.0}

        try:
            end_s = float(cfg.get("end_silence_s", 1.6))
        except Exception:
            end_s = 1.6
        try:
            grace = float(cfg.get("max_grace_s", 3.0))
        except Exception:
            grace = 3.0
        return record_until_silence(
            sample_rate=self.config.sample_rate,
            max_seconds=self.max_record_seconds,
            silence_seconds=end_s,
            threshold_multiplier=self.config.vad_threshold_multiplier,
            pause_judge=judge,
            extreme_silence_s=end_s + grace + 2.0,
            post_grace_s=float(cfg.get("post_grace_s", 0.0)),
        )

    # ── System message ────────────────────────────────────────────────────────

    def system_message(self, query: Optional[str] = None) -> Dict[str, str]:
        raw = self.config.persona["system_prompt"]
        text = "\n\n".join(raw) if isinstance(raw, list) else raw

        # Feature 24: long mode — on a long turn the override paragraph is
        # appended LAST (so it beats the earlier length rules) and the usual
        # short-cap line is not appended at all.  Sticky /long OR a one-turn
        # phrase trigger ("tell me everything" …).
        long_turn = False
        verb_cfg = None
        try:
            from core import verbosity as _vb
            verb_cfg = _vb.get_config()
            if verb_cfg.get("enabled", False):
                long_turn = bool(getattr(self, "_long_mode", False)) or \
                    _vb.phrase_hit(query, verb_cfg.get("phrases"))
        except Exception:
            long_turn = False
        if long_turn:
            try:
                text += "\n\n" + _vb.long_suffix(verb_cfg)
            except Exception:
                pass
        else:
            text += f" Keep replies under {self.config.persona['max_reply_length']} characters."

        if self.config.persona.get("mood_tags"):
            moods = ", ".join(self.config.persona.get("moods", []))
            text += (
                f" Begin every reply with one mood in square brackets, chosen from: {moods}."
            )

        reminder = self.config.persona.get("reminder", "")
        if reminder:
            text += f"\n\n{reminder}"

        # Follow-up context: remind her of the last thing that was said so she
        # can reference continuity without needing the full rolling history.
        if self.last_exchange:
            user_last, assist_last = self.last_exchange
            text += (
                f"\n\nLast exchange before this turn\n"
                f"User said: {user_last}\nYou said: {assist_last}"
            )

        # Memory context block (facts + closeness hint + episode summaries,
        # plus semantically relevant memories when the vector store is on).
        ctx = self.memory.build_context_block(
            max_facts=self.config.max_memory_facts, query=query)
        if ctx:
            text += f"\n\n{ctx}"

        # Feature 6: calibrate to the user's emotional tone.
        # Config: config/emotion_styles.json (enabled: false turns it off).
        # Feature 7: intimate-scene sound script.
        # Config: config/sound_scripts.json (enabled: false turns it off).
        matches = []
        if query:
            try:
                from emotion.style_detector import build_scene_block, detect as detect_styles
                matches = detect_styles(query)
                scene = build_scene_block(query, matches=matches)
                if scene:
                    text += f"\n\n{scene}"
            except Exception as e:
                logger.warning("[style] scene mood unavailable: %s", e)

            try:
                from emotion.sound_script import (SoundState, get_config as sound_cfg,
                                                  update_state)
                cfg_s = sound_cfg()
                if cfg_s.enabled:
                    state = getattr(self, "_sound_state", None)
                    if state is None:
                        state = SoundState()
                        self._sound_state = state
                    boundary = any(m.category == "Boundary" for m in matches)
                    note = update_state(state, query, boundary, cfg_s)
                    if note:
                        text += f"\n\n{note}"
            except Exception as e:
                logger.warning("[sound] sound script unavailable: %s", e)

        # Feature 27: feature-owned aliveness text (config/aliveness.yaml).
        # It is appended after the persona so turn-shape/listening/fallback
        # corrections beat the card's older habits without editing the card.
        try:
            from core import aliveness as _alive
            alive_block = _alive.prompt_block()
            if alive_block:
                text += f"\n\n{alive_block}"
        except Exception as e:
            logger.debug("[aliveness] prompt layer unavailable: %s", e)

        return {"role": "system", "content": text}

    # ── History management with summarization ─────────────────────────────────

    def _trim_history(self) -> None:
        """Keep enough history for both the context window and summarisation.

        Plain `history[-max_history:]` would silently drop messages before they
        are ever summarised when max_history < 2 * summarize_after.
        """
        keep = max(self.config.max_history, self.config.summarize_after * 2)
        self.history = self.history[-keep:]

    def _maybe_summarize(self) -> None:
        """If history exceeds summarize_after, compress the oldest half."""
        limit = self.config.summarize_after
        if len(self.history) < limit * 2:
            return

        to_summarize = self.history[:limit]
        self.history = self.history[limit:]

        logger.info(f"Summarising {len(to_summarize)} old messages…")
        summary = self.model.summarize(to_summarize, self.config.persona["name"])
        if summary:
            today = datetime.date.today().isoformat()
            self.memory.save_summary(today, summary)
            logger.info("Summary saved to episode store")

    # ── Commands ──────────────────────────────────────────────────────────────

    def try_command(self, text: str) -> Optional[str]:
        t = text.lower()

        # 3c: no leading zero on the hour
        if re.search(r"what time is it|what('s| is) the time", t):
            now = datetime.datetime.now().strftime("%I:%M %p").lstrip("0")
            return f"It's {now}."

        m = re.search(r"timer for (\d+)\s*(second|minute|hour)", t)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            secs = n * {"second": 1, "minute": 60, "hour": 3600}[unit]

            def _fire():
                msg = f"Your {n} {unit} timer is up!"
                self.speaker.say(msg)
                logger.info(f"Timer fired: {msg}")

            # 3a: daemon timer so it doesn't keep the process alive after quit
            timer = threading.Timer(secs, _fire)
            timer.daemon = True
            timer.start()
            return f"Okay, timer set for {n} {unit}{'s' if n != 1 else ''}."

        m = re.search(r"\bopen (\w+)", t)
        if m:
            name = m.group(1)
            if name in self.config.apps:
                # 3b: catch OSError so a missing executable doesn't crash the loop
                try:
                    subprocess.Popen(self.config.apps[name])
                except OSError as e:
                    logger.error(f"Could not open app '{name}': {e}")
                    return f"Sorry, I couldn't open {name}."
                return f"Opening {name}."
            if name in self.config.sites:
                webbrowser.open(self.config.sites[name])
                return f"Opening {name}."

        return None

    # ── Long mode toggles (Feature 24) ────────────────────────────────────────

    def _verb_command(self, text: str) -> bool:
        """Consume a /long (or /short) toggle. True when it was the command.

        Sticky for the session: /short (or another /long) is the only thing
        that changes it afterwards.  The confirm line is spoken (queued
        like any other sentence); the toggle is NOT a conversation turn —
        no history, no memory, no LLM.
        """
        try:
            from core import verbosity as _vb
            cfg = _vb.get_config()
        except Exception:
            return False
        if not cfg.get("enabled", False):
            return False
        act = _vb.parse_slash(text, cfg)
        if act is None:
            return False
        self._long_mode = (act == "on")
        line = cfg.get("feedback_on") if act == "on" else cfg.get("feedback_off")
        logger.info(f"[verbosity] long mode {'ON' if self._long_mode else 'OFF'}")
        if line:
            try:
                self.speaker.say(str(line))
            except Exception:
                pass
        return True

    # ── LLM streaming ─────────────────────────────────────────────────────────

    def stream_reply(self, messages: List[Dict[str, str]],
                     image_png: Optional[bytes] = None) -> Tuple[str, Optional[str]]:
        """Stream model reply, speaking incrementally. Returns (full_text, mood).

        Feature 4: when ``image_png`` is given it is attached to the last user
        message (provider-aware).
        """
        full = buf = head = ""
        mood: Optional[str] = None
        mood_done = False

        print(f" {self.config.persona['name']}: ", end="", flush=True)

        try:
            for chunk in self.model.stream_chat(messages, image_png=image_png):
                piece = chunk["message"]["content"]

                if not mood_done:
                    head += piece
                    parsed_mood, remaining = parse_mood_tag(head)
                    if parsed_mood is not None:
                        mood = parsed_mood
                        piece, mood_done = remaining, True
                    elif len(head.lstrip()) > 30:
                        piece, mood_done = head, True
                    else:
                        continue

                full += piece
                buf += piece
                print(piece, end="", flush=True)

                parts = split_sentences(buf, min_length=20)
                if len(parts) == 2:
                    complete, buf = parts
                    self._speak_apart(complete, mood)

            if not mood_done:
                full = buf = head
            if buf:
                self._speak_apart(buf, mood)

            print()
            return full.strip(), mood

        except Exception as e:
            logger.error(f"Model streaming failed: {e}", exc_info=True)
            fallback = next_fallback()
            print(f"\n  ({fallback})")
            self.speaker.say(fallback)
            if full:
                return full.strip(), mood
            raise

    def _speak_apart(self, text: str, mood: Optional[str] = None) -> None:
        """Features 28/29 (CLI speaking path): say only the voice part.

        Silent narration spans are printed instead (asterisks kept), memory
        notes vanish.  The narration/notes stay in ``full`` (history + log),
        exactly like the GUI path."""
        from core import narration as _narr
        from core import memory_notes as _mn
        voice, notes = _narr.split_voice(_mn.strip_notes(text))
        for note in notes:
            print(note)
        if voice.strip():
            self.speaker.say(voice, mood)

    # ── Turn handling ─────────────────────────────────────────────────────────

    def handle_user_input(self, text: str) -> None:
        print(f"👤 You: {text}")

        reply = self.try_command(text)
        if reply:
            print(f" {self.config.persona['name']}: {reply}")
            self.speaker.say(reply)
            self.memory.log_turn("user", text)
            self.memory.log_turn("assistant", reply)
            self.last_exchange = (text, reply)
            return

        self.history.append({"role": "user", "content": text})
        self._maybe_summarize()
        messages = [self.system_message(query=text)] + self.history[-self.config.max_history:]

        try:
            reply_text, mood = self.stream_reply(messages)
            self.history.append({"role": "assistant", "content": reply_text})
            self._trim_history()
            self.memory.log_turn("user", text)
            self.memory.log_turn("assistant", reply_text, mood)
            self.memory.after_turn()
            self.last_exchange = (text, reply_text)
            try:
                self._harvest_memory_notes(reply_text)
            except Exception as e:
                logger.debug(f"[self-notes] harvest skipped: {e}")
            self._extract_facts(text, reply_text)
        except Exception as e:
            logger.error(f"Turn failed: {e}")
            self.history.pop()

    # ── Intelligence helpers ──────────────────────────────────────────────────

    # ── Feature 18: dangling topics (threads) ────────────────────────────────

    def _extract_threads(self, user_text: str) -> None:
        """Quietly notice things that deserve a follow-up question later.

        One small LLM classification call per eligible user turn.  Off via
        config (config/threads.yaml `enabled: false`) — the default.  The
        store itself lives in core/threads.py (pure + persistence only).
        """
        try:
            from core import threads as _threads
        except ImportError:
            return
        try:
            cfg = _threads.get_config()
            if not cfg.get("enabled", False):
                return
            store = getattr(self, "_thread_store", None)
            if store is None:
                store = _threads.ThreadStore()
                self._thread_store = store
            if not hasattr(self, "_turns_since_thread_scan"):
                self._turns_since_thread_scan = 0
            self._turns_since_thread_scan += 1
            if self._turns_since_thread_scan < 2:   # every 2nd turn, cheap
                return
            self._turns_since_thread_scan = 0

            prompt = (
                "Does the user mention anything that will have a definite "
                "outcome LATER (appointments, deadlines, exams, plans, "
                "interviews, services, a message they expect)? Reply ONLY "
                "with JSON: {\"thread\": \"<short topic phrase>\"} or "
                "{\"thread\": null}. Never invent; only what they said. "
                "The phrase must work in the question 'how did X go?'.\n\n"
                f"User said: {user_text}"
            )
            full = ""
            for chunk in self.model.stream_chat(
                [{"role": "user", "content": prompt}], temperature=0.1
            ):
                full += chunk["message"]["content"]
            topic = _threads.parse_extraction(full)
            if topic and _threads.normalize(topic):
                added = store.open(topic, datetime.datetime.now(), cfg)
                if added:
                    logger.info(f"[threads] open: {topic!r}")
        except Exception as e:
            logger.debug(f"[threads] extraction skipped: {e}")

    def _web_context(self, user_text: str) -> Optional[str]:
        """Feature 20: fresh web knowledge for THIS turn, when it's wanted.

        One tiny LLM classification call per user turn while the feature is
        enabled (config/websearch.yaml — `enabled: false` by default); at
        most one DuckDuckGo query per turn; the same query is served from
        the TTL cache (config/websearch_cache.json) within `cache_hours`.
        Returns the SYSTEM-context block to append, or None (disabled,
        off-topic, or the search failed).  The engine lives in
        core/websearch.py (pure, testable, network-injectable).
        """
        try:
            if not user_text or not user_text.strip():
                return None
            from core import websearch as _web      # guarded, like threads
        except ImportError:
            return None
        try:
            cfg = _web.get_config()
            if not cfg.get("enabled", False):
                return None
            now = datetime.datetime.now()

            prompt = (
                "Would a live web search help answer this right now "
                "(news, weather, prices, sports scores, releases, current "
                "events, or facts about things newer than your knowledge)? "
                'Reply ONLY with JSON and nothing else: {"search": false} '
                'or {"search": true, "query": "<3-8 search words>"}.'
                f"\n\nUser said: {user_text.strip()[:400]}"
            )
            full = ""
            for chunk in self.model.stream_chat(
                [{"role": "user", "content": prompt}], temperature=0.1
            ):
                full += chunk["message"]["content"]
            want, query = _web.parse_decision(full)
            if not want or not query:
                return None
            cache = getattr(self, "_web_cache", None)
            if cache is None:
                cache = _web.SearchCache()
                self._web_cache = cache
            hits = _web.perform(query, cfg, cache=cache, now=now)
            logger.info(f"[websearch] {query!r}: {len(hits)} result(s)")
            return _web.format_results(query, hits, now=now, cfg=cfg)
        except Exception as e:
            logger.debug(f"[websearch] skipped: {e}")
            return None

    def _extract_facts(self, user_text: str, reply_text: str) -> None:
        """Ask the model to pull durable facts about the user out of a turn.

        Runs every few turns (not each turn) to limit token/API cost. Facts are
        deduped against existing memory and persisted via config.save_memory.
        """
        if not self.config.auto_extract_facts:
            return
        self._turns_since_fact_scan += 1
        if self._turns_since_fact_scan < self._FACT_SCAN_EVERY:
            return
        self._turns_since_fact_scan = 0

        try:
            prompt = (
                "Extract durable facts the USER explicitly stated about "
                "themselves in this exchange. Write each fact as a standalone "
                "third-person sentence starting with 'The user'. Keep only "
                "preferences, routines, people, dates, places, and ongoing "
                "projects. Exclude greetings, questions, plans for today, "
                "temporary feelings, anything the assistant said, instructions "
                "about tools/internet/roleplay, and anything you inferred. "
                "Output ONLY a JSON list of strings, or [] if there are none.\n\n"
                f"User: {user_text}\nYou: {reply_text}"
            )
            full = ""
            for chunk in self.model.stream_chat(
                [{"role": "user", "content": prompt}], temperature=0.1
            ):
                full += chunk["message"]["content"]

            from core.memory_hygiene import clean_fact, parse_facts
            known = [clean_fact(f) for f in self.memory.get_facts()]
            known_keys = {f.casefold() for f in known if f}
            added = 0
            for fact in parse_facts(full):
                if fact.casefold() in known_keys:
                    continue
                self.memory.add_fact(fact)
                known_keys.add(fact.casefold())
                added += 1
            if added:
                self.config.save_memory(self.memory.get_facts())
                logger.info(f"Auto-extracted {added} new fact(s)")
        except Exception as e:
            logger.debug(f"Fact extraction skipped: {e}")

    _MISSED_PHRASES = [
        "Sorry, I didn't catch that. Say it again?",
        "Huh? You went quiet on me. Try once more?",
        "I missed that, baby. One more time?",
    ]

    def _missed_ack(self) -> None:
        """Speak a gentle acknowledgment when a record/transcription comes up empty."""
        if not getattr(self.config, "gentle_missed", True):
            return
        if not self.speaker.enabled:
            print("(didn't catch that)")
            return
        self.speaker.say(random.choice(self._MISSED_PHRASES))

    # ── CLI commands ──────────────────────────────────────────────────────────

    def cmd_remember(self, fact: str) -> None:
        if not fact:
            print("Usage: /remember <fact>")
            return
        self.memory.add_fact(fact)
        self.config.save_memory(self.memory.get_facts())
        print(f"✓ Remembered: {fact}")

    def cmd_forget(self) -> None:
        self.memory.clear_facts()
        self.config.save_memory([])
        print("✓ Memory facts cleared")

    def cmd_mute(self) -> None:
        self.speaker.enabled = not self.speaker.enabled
        print(f" Voice {'off' if not self.speaker.enabled else 'on'}")

    def cmd_volume(self, arg: str) -> None:
        try:
            self.speaker.set_volume(float(arg))
            print(f" Volume: {self.speaker.volume:.0%}")
        except ValueError:
            print("Usage: /volume <0.0–1.0>")

    def cmd_speed(self, arg: str) -> None:
        try:
            self.speaker.set_speed(float(arg))
            print(f" Speed: {self.speaker.speed:.1f}x")
        except ValueError:
            print("Usage: /speed <0.5–2.0>")

    def cmd_timer(self, arg: str) -> None:
        try:
            self.max_record_seconds = float(arg)
            print(f"⏱  Max recording: {self.max_record_seconds}s")
        except ValueError:
            print("Usage: /t <seconds>")

    def cmd_reload(self) -> None:
        try:
            self.config.reload()
            self.apply_config()
            print(f"✓ Personality reloaded: {self.config.persona['name']}")
        except Exception as e:
            print(f"✗ Reload failed: {e}")

    def cmd_stats(self) -> None:
        tokens = self.model.get_token_stats()
        print(f"\n Statistics:")
        print(f"   Closeness: {self.memory.closeness_score()} pts  ({self.memory.tier_name()})")
        print(f"   Memory facts: {len(self.memory.get_facts())}")
        print(f"   History: {len(self.history)} messages")
        print(f"   Tokens: {tokens['prompt']} prompt  {tokens['completion']} completion")
        backend = "Cerebras" if self.config.use_cerebras else "Ollama"
        print(f"   Backend: {backend} / {self.model.model_name}")
        if self.speaker.cache:
            print(f"   TTS cache: {len(self.speaker.cache.cache)}/{self.speaker.cache.max_size}")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        # Greeting — pick randomly from the greetings list each startup
        greeting = self.config.pick_greeting()
        if greeting:
            print(f" {self.config.persona['name']}: {greeting}")
            self.speaker.say(greeting)
            self.memory.log_turn("assistant", greeting)

        print("\nCommands:")
        print("  Enter              record voice")
        print("  text + Enter       text message")
        print("  /remember <fact>   /forget   /mute   /volume <0-1>")
        print("  /speed <0.5-2.0>   /reload   /stats  /t <sec>  /quit\n")

        while self.running:
            try:
                woke = False
                if self.wake_detector:
                    print("💤 Waiting for wake word…")
                    self.wake_detector.wait_for_wake()
                    print("⚡ Wake word detected!")
                    woke = True

                # In wake-word mode text commands are unavailable; cmd is always ""
                # which falls straight through to voice recording below.
                cmd = "" if woke else input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                break

            self.speaker.stop()

            if cmd.startswith("/"):
                name, _, arg = cmd.partition(" ")
                if name == "/quit":
                    break
                elif name == "/mute":
                    self.cmd_mute()
                elif name == "/forget":
                    self.cmd_forget()
                elif name == "/remember":
                    self.cmd_remember(arg)
                elif name in ("/volume", "/v"):
                    self.cmd_volume(arg)
                elif name == "/speed":
                    self.cmd_speed(arg)
                elif name in ("/t", "/timer"):
                    self.cmd_timer(arg)
                elif name == "/reload":
                    self.cmd_reload()
                elif name == "/stats":
                    self.cmd_stats()
                elif name in ("/long", "/short"):
                    self._verb_command(name)
                else:
                    print(f" Unknown command: {name}")
                continue

            if cmd:
                self.handle_user_input(cmd)
                continue

            # Voice recording (Features 21+22 wrapper; old capture when off)
            print("🎤 Listening…")
            audio = self._record_voice()
            if audio is None or len(audio) / self.config.sample_rate < self.config.min_audio_length:
                self._missed_ack()
                continue

            text = self.transcribe(audio)
            if not text:
                self._missed_ack()
                continue

            self.handle_user_input(text)

        print("\nBye!")


def main() -> None:
    config_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        config = Config(config_dir=config_dir)
    except (FileNotFoundError, ValueError, TypeError) as e:
        print(f"[Config error] {e}")
        sys.exit(1)

    companion = VoiceCompanion(config)
    try:
        companion.run()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        companion.shutdown()


if __name__ == "__main__":
    main()
