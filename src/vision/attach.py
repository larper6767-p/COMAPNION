"""Provider-aware image attachment for chat requests (Feature 4).

Pure functions: given an OpenAI-style ``messages`` list and PNG bytes, return a
*new* list with the image attached to the last user message.

* Cerebras / OpenAI  -> a content-parts list with an ``image_url`` data URL.
* Ollama             -> the message keeps its string content and gains an
  ``images`` list of base64 strings.

The caller's list and messages are never mutated.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

# Appended to the user's text so the model knows why an image is present.
DEFAULT_VISION_HINT = (
    "The user just shared a screenshot of their screen. "
    "Look at it and respond naturally, in character."
)


def to_b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def _last_user_index(messages: List[Dict[str, Any]]) -> Optional[int]:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            return i
    return None


def attach_image(
    messages: List[Dict[str, Any]],
    png: bytes,
    use_cerebras: bool,
    hint: str = DEFAULT_VISION_HINT,
) -> List[Dict[str, Any]]:
    """Return a copy of ``messages`` with ``png`` attached to the last user turn."""
    b64 = to_b64(png)
    out: List[Dict[str, Any]] = [dict(m) for m in messages]

    idx = _last_user_index(out)
    if idx is None:
        out.append({"role": "user", "content": hint})
        idx = len(out) - 1

    src = out[idx]
    content = src.get("content")

    # Normalise existing content into text parts.
    if isinstance(content, list):
        parts = [dict(p) for p in content]
    else:
        base = str(content or "").strip()
        text = (base + "\n\n" + hint).strip() if base else hint
        parts = [{"type": "text", "text": text}]

    msg = dict(src)

    if use_cerebras:
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
        msg["content"] = parts
    else:
        # Ollama: string content + a sibling base64 image list.
        plain = "\n\n".join(
            p.get("text", "") for p in parts
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
        msg["content"] = plain
        images = list(msg.get("images") or [])
        images.append(b64)
        msg["images"] = images

    out[idx] = msg
    return out


__all__ = ["attach_image", "to_b64", "DEFAULT_VISION_HINT"]
