"""Load Whisper from the local cache before touching the HuggingFace Hub.

``WhisperModel`` normally resolves the model revision through the Hub on every
launch, even when the files are already cached.  That added two needless
network checks to warm startup.  Try the cache first; only download when the
model genuinely is not present.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)


def load_cached(
    model_cls: Any,
    model_size_or_path: str,
    *,
    device: str = "auto",
    compute_type: str = "default",
    log: Optional[logging.Logger] = None,
) -> Tuple[Any, bool]:
    """Return ``(model, used_cache)``.

    The first attempt passes ``local_files_only=True``.  If the cache is
    missing, retry normally so first-run setup still downloads the model.
    """
    log = log or logger
    try:
        model = model_cls(
            model_size_or_path,
            device=device,
            compute_type=compute_type,
            local_files_only=True,
        )
        return model, True
    except Exception as exc:
        log.info("Whisper model not cached; downloading (%s)", exc)
        model = model_cls(
            model_size_or_path,
            device=device,
            compute_type=compute_type,
            local_files_only=False,
        )
        return model, False


__all__ = ["load_cached"]
