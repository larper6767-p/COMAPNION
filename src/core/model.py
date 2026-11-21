"""LLM interface: Cerebras cloud or Ollama local, with streaming, summarization, and retry."""
import os
import re
import logging
from typing import Dict, List, Optional, Tuple, Generator, Any

import ollama
from cerebras.cloud.sdk import Cerebras
from dotenv import load_dotenv

from body_tags import resolve_mood

load_dotenv()

logger = logging.getLogger(__name__)

# Cerebras accepts at most four stop sequences.  The character card carries
# five; trim deterministically and report the truncation once at INFO instead
# of emitting a scary warning on every launch.
CEREBRAS_STOP_LIMIT = 4


def cap_stop_sequences(stop: Optional[List[str]],
                       limit: int = CEREBRAS_STOP_LIMIT) -> List[str]:
    """Deduplicate and cap provider stop sequences, preserving order."""
    out: List[str] = []
    seen = set()
    for raw in stop or []:
        if not isinstance(raw, str):
            continue
        item = raw
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= max(1, int(limit)):
            break
    return out


SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")

# ── Graceful error messages in Evelyn's voice ─────────────────────────────────
_FALLBACK_MESSAGES = [
    "Ugh, my brain just glitched out. Try again?",
    "Okay that definitely didn't work. One more time?",
    "Something broke up there, sorry! What were you saying?",
]
_fallback_idx = 0


def next_fallback() -> str:
    global _fallback_idx
    msg = _FALLBACK_MESSAGES[_fallback_idx % len(_FALLBACK_MESSAGES)]
    _fallback_idx += 1
    return msg


class ModelInterface:
    """Wrapper for Cerebras or Ollama with streaming, token tracking, and summarization."""

    def __init__(
        self,
        model_name: str,
        temperature: float = 0.7,
        use_cerebras: bool = True,
        max_tokens: int = 300,
        repeat_penalty: float = 1.1,
        stop: Optional[List[str]] = None,
        vision_model: str = "",
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.use_cerebras = use_cerebras
        self.max_tokens = max_tokens
        self.repeat_penalty = repeat_penalty
        original_stop = list(stop or [])
        self.stop = original_stop
        # Feature 4: optional model used for image turns ("" = reuse model_name).
        self.vision_model = (vision_model or "").strip()
        if self.vision_model:
            logger.info(f"Vision model configured: {self.vision_model}")
        self.total_tokens: Dict[str, int] = {"prompt": 0, "completion": 0}
        # Phase 1: per-request usage, separate from the legacy cumulative/display
        # counters above.  The prompt-report finalizer reads this after the
        # streaming call so estimates can be checked against provider numbers.
        self.last_usage: Dict[str, int] = {"prompt": 0, "completion": 0}
        self.last_finish_reason: str = ""

        if use_cerebras:
            api_key = os.environ.get("CEREBRAS_API_KEY")
            if not api_key:
                raise ValueError(
                    "CEREBRAS_API_KEY not found in environment. Add it to your .env file."
                )
            self.cerebras_client = Cerebras(api_key=api_key)
            logger.info(f"Cerebras API ready — model: {self.model_name}")
            # Cerebras allows at most 4 stop sequences.  The card carries five;
            # this is an expected, harmless trim, not a warning.
            self.stop = cap_stop_sequences(original_stop)
            if len(self.stop) < len(original_stop):
                logger.info(
                    "Cerebras stop sequences limited to %d (from %d)",
                    len(self.stop), len(original_stop))
        else:
            self.cerebras_client = None
            self._validate_ollama_model()

    # ── Public API ────────────────────────────────────────────────────────────

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        image_png: Optional[bytes] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Stream chat completion. Yields Ollama-compatible chunk dicts.

        Each chunk: {"message": {"content": str}, "done": bool}
        Raises on failure after logging — caller should catch and use next_fallback().

        Feature 4: when ``image_png`` is given it is attached to the last user
        message (provider-aware) and ``vision_model`` is used if configured.
        """
        temp = temperature if temperature is not None else self.temperature
        # Phase 1: reset per-request usage before every call (including the
        # small classification/summary calls).
        self.last_usage = {"prompt": 0, "completion": 0}
        self.last_finish_reason = ""

        model = None
        if image_png:
            from vision.attach import attach_image

            messages = attach_image(messages, image_png, use_cerebras=self.use_cerebras)
            model = self.vision_model or None
            logger.info("[vision] screenshot attached (model=%s)", model or self.model_name)

        if self.use_cerebras:
            yield from self._stream_cerebras(messages, temp, model=model)
        else:
            yield from self._stream_ollama(messages, temp, model=model)

    def summarize(
        self,
        messages: List[Dict[str, str]],
        character_name: str = "the assistant",
    ) -> str:
        """Summarize a block of conversation history into a compact paragraph.

        Used to compress old turns before they would be dropped.
        Returns the summary string, or an empty string on failure.
        """
        if not messages:
            return ""

        transcript = "\n".join(
            f"{m['role'].capitalize()}: {m['content']}" for m in messages
        )
        prompt = (
            f"Summarize the following conversation between a user and {character_name} "
            "in 3–5 concise bullet points, preserving key facts, events, and emotional moments. "
            "Write only the bullet points, nothing else.\n\n"
            f"{transcript}"
        )
        summary_messages = [{"role": "user", "content": prompt}]

        # Use a low temperature for factual summarization
        try:
            full = ""
            for chunk in self.stream_chat(summary_messages, temperature=0.2):
                full += chunk["message"]["content"]
            summary = full.strip()
            logger.info(f"Summarized {len(messages)} messages into {len(summary)} chars")
            return summary
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return ""

    def get_token_stats(self) -> Dict[str, int]:
        return self.total_tokens.copy()

    def reset_token_stats(self) -> None:
        self.total_tokens = {"prompt": 0, "completion": 0}

    def get_last_usage(self) -> Dict[str, int]:
        """Return the provider usage for the most recent stream_chat call."""
        return dict(getattr(self, "last_usage", {}) or {})

    def get_last_finish_reason(self) -> str:
        """Return the most recent finish reason, or "" when unavailable."""
        return str(getattr(self, "last_finish_reason", "") or "")

    # ── Private ───────────────────────────────────────────────────────────────

    def _stream_cerebras(
        self, messages: List[Dict[str, str]], temp: float, model: Optional[str] = None
    ) -> Generator[Dict[str, Any], None, None]:
        try:
            kwargs: Dict[str, Any] = dict(
                model=model or self.model_name,
                messages=messages,
                stream=True,
                temperature=temp,
                max_completion_tokens=self.max_tokens,
                # Keep reasoning OFF — we don't need chain-of-thought for a
                # voice companion, and it would only delay delta.content while
                # the think block runs.
                extra_body={"reasoning_effort": "low"},
            )
            if self.stop:
                kwargs["stop"] = self.stop

            stream = self.cerebras_client.chat.completions.create(**kwargs)

            for chunk in stream:
                # Capture usage even on a final chunk that carries no choices;
                # do it BEFORE yielding so an interrupted consumer cannot skip
                # the counters.
                usage = getattr(chunk, "usage", None)
                if usage:
                    if getattr(usage, "prompt_tokens", None) is not None:
                        prompt_tokens = int(usage.prompt_tokens or 0)
                        self.total_tokens["prompt"] = prompt_tokens
                        self.last_usage["prompt"] = prompt_tokens
                    if getattr(usage, "completion_tokens", None) is not None:
                        completion_tokens = int(usage.completion_tokens or 0)
                        self.total_tokens["completion"] = completion_tokens
                        self.last_usage["completion"] = completion_tokens

                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason:
                    self.last_finish_reason = str(finish_reason)
                delta = choice.delta
                # reasoning chunks arrive in delta.reasoning — skip them silently.
                # actual response text arrives in delta.content once thinking is done.
                content = (delta.content or "") if hasattr(delta, "content") else ""
                done = finish_reason is not None
                if not content and not done:
                    continue  # pure reasoning token, nothing to yield yet
                yield {"message": {"content": content}, "done": done}

        except Exception as e:
            logger.error(f"Cerebras streaming failed: {e}")
            raise

    def _stream_ollama(
        self, messages: List[Dict[str, str]], temp: float, model: Optional[str] = None
    ) -> Generator[Dict[str, Any], None, None]:
        try:
            opts: Dict[str, Any] = {
                "temperature": temp,
                "num_ctx": 2048,
                "num_gpu": 1,
                "repeat_penalty": self.repeat_penalty,
            }
            if self.stop:
                opts["stop"] = self.stop

            for chunk in ollama.chat(
                model=model or self.model_name,
                messages=messages,
                stream=True,
                options=opts,
            ):
                # Capture counters before yielding so interrupted consumers
                # still leave the per-request usage readable.
                if "prompt_eval_count" in chunk:
                    prompt_tokens = int(chunk["prompt_eval_count"] or 0)
                    self.total_tokens["prompt"] += prompt_tokens
                    self.last_usage["prompt"] = prompt_tokens
                if "eval_count" in chunk:
                    completion_tokens = int(chunk["eval_count"] or 0)
                    self.total_tokens["completion"] += completion_tokens
                    self.last_usage["completion"] = completion_tokens
                done_reason = chunk.get("done_reason")
                if done_reason:
                    self.last_finish_reason = str(done_reason)
                yield chunk

        except Exception as e:
            logger.error(f"Ollama streaming failed: {e}")
            raise

    def _validate_ollama_model(self) -> None:
        try:
            result = ollama.list()
            available = [m.model for m in result.get("models", [])]
            if not any(self.model_name.startswith(n) for n in available):
                logger.warning(
                    f"Model '{self.model_name}' not in Ollama. "
                    f"Available: {', '.join(available)}"
                )
            else:
                logger.info(f"Ollama model '{self.model_name}' validated")
        except Exception as e:
            logger.warning(f"Could not validate Ollama model (is Ollama running?): {e}")


# ── Text helpers ──────────────────────────────────────────────────────────────

def parse_mood_tag(text: str) -> Tuple[Optional[str], str]:
    """Extract a leading mood tag.  Returns (mood_or_None, remaining_text).

    Handles both the legacy bare form (``[joyful]``) and the explicit form
    (``[mood:joyful]`` / alias ``[mood:happy]``) through ONE shared resolver
    (body_tags.resolve_mood), so they all resolve identically.  Unknown or
    disabled moods return None and the bracket is NOT stripped.
    """
    text = text.lstrip()
    if text.startswith("[") and "]" in text:
        end = text.index("]")
        raw = text[1:end].strip()
        if raw and raw.lower() != "clear":
            mood = resolve_mood(raw)
            if mood is not None:
                return mood, text[end + 1:].lstrip()
        return None, text
    return None, text


def split_sentences(text: str, min_length: int = 20) -> List[str]:
    """Split into [complete_part, incomplete_tail] for incremental TTS.

    Returns a one-element list when there is nothing to split yet.
    """
    parts = SENTENCE_END.split(text)
    if len(parts) > 1 and len(" ".join(parts[:-1])) >= min_length:
        return [" ".join(parts[:-1]), parts[-1]]
    return [text]
