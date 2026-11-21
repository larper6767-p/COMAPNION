"""Audio recording: VAD, per-turn recalibration, interrupt detection, wake-word stub."""
import re
import time
import logging
import threading
from collections import deque
from typing import Any, Callable, Optional

import numpy as np

try:
    import sounddevice as sd
    _SOUNDDEVICE_ERROR: Optional[BaseException] = None
except Exception as exc:  # headless CI / no PortAudio host
    sd = None  # type: ignore[assignment]
    _SOUNDDEVICE_ERROR = exc

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(block))))


def calibrate_noise(stream: Any, block: int, samples: int = 15) -> float:
    """Measure ambient noise floor and return a safe threshold."""
    readings = [rms(stream.read(block)[0]) for _ in range(samples)]
    noise = float(np.mean(readings))
    threshold = max(noise * 3.0, 0.01)
    logger.debug(f"Noise floor: {noise:.4f}  threshold: {threshold:.4f}")
    return threshold


def wake_word_matches(text: str, wake_word: str) -> bool:
    """Return True only when the *entire* wake phrase is present as words.

    The Whisper fallback used to accept any substring (so "evelyn" matched
    "eve") and only half of a multi-word phrase (so a lone "hello" matched
    "hello eve").  Normalise punctuation/whitespace and require the whole
    phrase, with word boundaries on both sides.
    """
    def _normalise(value: str) -> str:
        words = re.sub(r"[^\w]+", " ", str(value).casefold()).split()
        return " ".join(words)

    phrase = _normalise(wake_word)
    spoken = _normalise(text)
    if not phrase or not spoken:
        return False
    return re.search(
        rf"(?<!\w){re.escape(phrase)}(?!\w)", spoken
    ) is not None


# ── Core recording ────────────────────────────────────────────────────────────

def record_until_silence(
    sample_rate: int = 16000,
    max_seconds: float = 20.0,
    silence_seconds: float = 2.0,
    wait_seconds: float = 8.0,
    threshold_multiplier: float = 3.0,
    interrupt_event: Optional[threading.Event] = None,
    preroll_s: float = 0.24,
    pause_judge: Optional[Callable[[np.ndarray, float], Optional[dict]]] = None,
    extreme_silence_s: float = 40.0,
    post_grace_s: float = 0.0,
) -> Optional[np.ndarray]:
    """Record when voice is detected; stop on silence or interrupt signal.

    Args:
        sample_rate: Audio sample rate in Hz.
        max_seconds: Hard cap on recording duration.
        silence_seconds: Silence duration before auto-stop (1.5s gives natural
                         pauses room to breathe without cutting off mid-sentence).
        wait_seconds: How long to wait for voice before giving up.
        threshold_multiplier: Multiplier applied to ambient noise floor.
        interrupt_event: If set and becomes True mid-recording, stop early.
        preroll_s (Feature 21): look-back seconds for catching the first
                       syllable (was a fixed 240 ms).
        pause_judge (Feature 21): OPTIONAL endpointing callback.  When given,
                       the FIXED silence break is DISABLED and instead the
                       judge is consulted every ``recheck_s`` (0.6 s) of
                       silence as ``judge(audio_sofar, silence_seconds)``;
                       it returns ``{"dispatch": bool, "mute_s": float}``
                       (or None → keep going).  The recorder drops
                       ``mute_s`` seconds of mic tail right after — the
                       window her backchannel clip plays through without
                       being captured.  The loop then breaks only when the
                       judge says dispatch, or at ``extreme_silence_s``
                       (the never-hangs safety net) or max_seconds.
        post_grace_s (Feature 25): AFTER she decides you're done — the plain
                       silence threshold met, or the judge said dispatch —
                       she keeps listening this many extra seconds.  Speak
                       again inside the window and she folds it into the
                       SAME recording (the cutoff is reversed and the whole
                       listen/think loop continues); stay quiet and the
                       stop fires a few seconds later.  0.0 = off (the old
                       cut-it-at-threshold behavior).

    Returns:
        Float32 audio array, or None if nothing captured.
    """
    if sd is None:
        logger.error("Microphone unavailable: %s", _SOUNDDEVICE_ERROR)
        return None

    block = int(sample_rate * 0.03)       # 30 ms per block
    needed_silence = int(silence_seconds / 0.03)
    recheck_blocks = max(1, int(round(0.6 / 0.03)))
    preroll: deque = deque(maxlen=max(8, int(round(preroll_s / 0.03))))
    chunks, started, silent = [], False, 0
    mute_blocks_left = 0
    # Feature 25: set the moment she DECIDED you're done — she keeps
    # listening until post_grace_s elapses (or you speak again, which
    # reverts the decision and continues the same recording).
    stop_scheduled, grace_until = False, 0

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=block,
        ) as stream:
            # Re-calibrate every turn against current ambient noise
            threshold = calibrate_noise(stream, block)

            t0 = time.time()
            while True:
                data, _ = stream.read(block)
                data = data.copy()
                level = rms(data)

                if level > threshold:
                    if not started:
                        started = True
                        chunks.extend(preroll)
                        logger.debug("Voice activity detected")
                    silent = 0
                    if stop_scheduled:
                        # Feature 25: you spoke again inside the after-grace —
                        # the cutoff decision is REVERSED, the same recording
                        # continues, and the loop keeps listening.
                        stop_scheduled = False
                        grace_until = 0
                        logger.debug(
                            "Voice resumed during post-grace — still listening")
                elif started:
                    silent += 1

                if started:
                    if mute_blocks_left > 0:
                        # Feature 21/22: her backchannel just played at the
                        # speakers; drop the echoed mic tail so it never
                        # reaches Whisper's input (the pause counts on).
                        mute_blocks_left -= 1
                    else:
                        chunks.append(data)
                    if silent >= needed_silence:
                        if pause_judge is None:
                            if stop_scheduled:
                                if silent >= grace_until:
                                    logger.debug(
                                        f"Silence stop at {time.time() - t0:.1f}s"
                                        " (post-grace elapsed)")
                                    break
                            elif post_grace_s > 0:
                                stop_scheduled = True
                                grace_until = silent + int(
                                    round(post_grace_s / 0.03))
                                logger.debug(
                                    f"Silence threshold met — listening a "
                                    f"few extra seconds ({post_grace_s:.1f}s)")
                            else:
                                logger.debug(
                                    f"Silence stop at {time.time() - t0:.1f}s")
                                break
                        # A judge owns the endpointing now: the FIXED break
                        # is disabled between rechecks.  The extreme-silence
                        # net below stops a judge that never lets go.
                        if silent * 0.03 >= extreme_silence_s:
                            logger.debug(
                                f"Extreme-silence stop at {time.time() - t0:.1f}s")
                            break
                    if (pause_judge is not None
                            and not stop_scheduled
                            and silent > 0
                            and silent % recheck_blocks == 0):
                        try:
                            res = pause_judge(
                                (np.concatenate(chunks).flatten()
                                 if chunks else np.zeros(0, np.float32)),
                                silent * 0.03)
                        except Exception as judge_err:
                            logger.debug(f"pause_judge failed: {judge_err}")
                            res = {"dispatch": False}
                        if isinstance(res, dict):
                            if res.get("dispatch"):
                                if post_grace_s > 0:
                                    stop_scheduled = True
                                    grace_until = silent + int(
                                        round(post_grace_s / 0.03))
                                    logger.debug(
                                        "Judge said done — a post-grace of "
                                        f"{post_grace_s:.1f}s before truly "
                                        "stopping")
                                else:
                                    logger.debug(
                                        f"Judged stop at {time.time() - t0:.1f}s")
                                    break
                            try:
                                extra = max(
                                    0.0, float(res.get("mute_s", 0.0)))
                            except Exception:
                                extra = 0.0
                            mute_blocks_left = max(
                                mute_blocks_left,
                                int(round(extra / 0.03)))
                else:
                    preroll.append(data)
                    if time.time() - t0 > wait_seconds:
                        logger.debug("Timeout: no voice detected")
                        return None

                if time.time() - t0 > max_seconds:
                    logger.debug(f"Max duration {max_seconds}s reached")
                    break

                # External interrupt (e.g. user started speaking while AI was talking)
                if interrupt_event and interrupt_event.is_set():
                    logger.debug("Recording interrupted by external event")
                    break

        if not chunks:
            return None

        audio = np.concatenate(chunks).flatten()
        duration = len(audio) / sample_rate
        logger.info(f"Recorded {duration:.1f}s")
        return audio

    except Exception as e:
        logger.error(f"Recording failed: {e}")
        return None


# ── Parallel interrupt monitor ────────────────────────────────────────────────

class InterruptMonitor:
    """Listens in the background; sets an event the moment the user starts speaking.

    Usage:
        monitor = InterruptMonitor(sample_rate=16000)
        monitor.start()
        # ... AI is speaking ...
        if monitor.triggered:
            speaker.stop()
        monitor.stop()
    """

    def __init__(self, sample_rate: int = 16000, threshold_multiplier: float = 3.5):
        self.sample_rate = sample_rate
        self.threshold_multiplier = threshold_multiplier
        self._event = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def triggered(self) -> bool:
        return self._event.is_set()

    def trip(self) -> None:
        """Set the interrupted flag DIRECTLY (Feature 19: a typed message
        cutting in; the streaming loops poll ``triggered``)."""
        self._event.set()

    def start(self) -> None:
        self._event.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def reset(self) -> None:
        self._event.clear()

    def _run(self) -> None:
        block = int(self.sample_rate * 0.03)
        if sd is None:
            logger.debug("Interrupt monitor disabled: %s", _SOUNDDEVICE_ERROR)
            return
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=block,
            ) as stream:
                noise_samples = [rms(stream.read(block)[0]) for _ in range(12)]
                noise = float(np.mean(noise_samples))
                threshold = max(noise * self.threshold_multiplier, 0.015)

                while not self._stop.is_set():
                    data, _ = stream.read(block)
                    if rms(data) > threshold:
                        logger.debug("Interrupt monitor: voice detected")
                        self._event.set()
                        break
        except Exception as e:
            logger.debug(f"InterruptMonitor error: {e}")


# ── Wake-word detection (text-based fallback + openwakeword hook) ─────────────

class WakeWordDetector:
    """Lightweight wake-word detector.

    Strategy:
    1. Try to import openwakeword (pip install openwakeword).
       If available, use it for real always-on detection.
    2. Fall back to a simple energy + transcription check:
       record a short clip, run Whisper on it, match against keywords.

    The caller drives the loop; call ``wait_for_wake()`` which blocks
    until the wake word is heard, then returns.
    """

    def __init__(
        self,
        wake_word: str = "hello eve",
        sample_rate: int = 16000,
        transcribe_fn: Optional[Callable[[np.ndarray], str]] = None,
    ):
        self.wake_word = wake_word.lower()
        self.sample_rate = sample_rate
        self.transcribe_fn = transcribe_fn  # injected by VoiceCompanion
        self._use_oww = False

        # Try openwakeword
        try:
            from openwakeword.model import Model  # type: ignore
            self._oww = Model(wakeword_models=[], inference_framework="onnx")
            self._use_oww = True
            logger.info("openwakeword loaded — using neural wake-word detection")
        except Exception:
            logger.info(
                "openwakeword not available — using Whisper-based wake-word detection. "
                "Install with: pip install openwakeword"
            )

    def wait_for_wake(self) -> None:
        """Block until the wake word is detected."""
        if self._use_oww:
            self._oww_loop()
        else:
            self._whisper_loop()

    # ── openwakeword path ─────────────────────────────────────────────────────

    def _oww_loop(self) -> None:
        chunk_size = 1280  # openwakeword expects 80 ms at 16 kHz
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=chunk_size,
            ) as stream:
                while True:
                    data, _ = stream.read(chunk_size)
                    pcm = data.flatten()
                    prediction = self._oww.predict(pcm)
                    for model_name, score in prediction.items():
                        if score > 0.5:
                            logger.info(f"Wake word detected via openwakeword (score={score:.2f})")
                            return
        except Exception as e:
            logger.error(f"openwakeword loop error: {e}")
            # Fall through to Whisper loop
            self._whisper_loop()

    # ── Whisper fallback path ─────────────────────────────────────────────────

    def _whisper_loop(self) -> None:
        """Poll with short recordings, check transcript for the whole phrase."""
        logger.debug(f"Wake-word phrase: {self.wake_word!r}")
        if sd is None:
            logger.error(
                "Wake word disabled: microphone unavailable (%s)",
                _SOUNDDEVICE_ERROR,
            )
            return

        while True:
            # Record a short burst (up to 2.5 s) looking for any speech energy
            audio = _record_short(self.sample_rate, max_seconds=2.5)
            if audio is None:
                continue

            if self.transcribe_fn is None:
                # No transcriber available — any voice triggers
                logger.debug("No transcriber; any voice triggers wake")
                return

            text = self.transcribe_fn(audio).lower()
            if not text:
                continue

            if wake_word_matches(text, self.wake_word):
                logger.info(f"Wake word '{self.wake_word}' detected in: '{text}'")
                return


def _record_short(sample_rate: int, max_seconds: float = 2.5) -> Optional[np.ndarray]:
    """Record a short clip only if voice energy is present."""
    if sd is None:
        return None
    block = int(sample_rate * 0.03)
    chunks = []
    try:
        with sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="float32", blocksize=block
        ) as stream:
            threshold = calibrate_noise(stream, block, samples=5)
            t0 = time.time()
            voice_seen = False
            while time.time() - t0 < max_seconds:
                data, _ = stream.read(block)
                data = data.copy()
                if rms(data) > threshold:
                    voice_seen = True
                if voice_seen:
                    chunks.append(data)
        if not chunks:
            return None
        return np.concatenate(chunks).flatten()
    except Exception:
        return None
