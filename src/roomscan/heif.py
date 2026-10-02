"""HEIC / HEIF support (iPhone photos), loaded once and optional.

pillow-heif is a compiled extension. Where it cannot load (not installed, or a machine policy
such as Windows Application Control blocks its DLL), every other format must keep working
and a HEIC photo must fail with a clear, actionable message rather than crash the run.
"""
from __future__ import annotations

_state: dict = {}

HINT = ("HEIC photos cannot be read on this machine (the HEIC decoder is unavailable: {why}). "
        "Set the iPhone to Settings > Camera > Formats > Most Compatible (JPEG), or convert the photos to JPEG")


def register() -> bool:
    """Make PIL open HEIC / HEIF whatever the file is called. True if available."""
    if "ok" not in _state:
        try:
            import os

            import pillow_heif
            pillow_heif.register_heif_opener()
            # iPhone HEIC is a grid of 512 px tiles: decode them in parallel (default 4 threads)
            pillow_heif.options.DECODE_THREADS = max(pillow_heif.options.DECODE_THREADS, min(8, os.cpu_count() or 4))
            _state["ok"], _state["why"] = True, ""
        except Exception as e:  # ImportError, or the DLL refused by policy (also an ImportError subclass)
            _state["ok"], _state["why"] = False, f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
    return _state["ok"]


def unavailable_reason() -> str:
    register()
    return _state.get("why", "")


def hint() -> str:
    return HINT.format(why=unavailable_reason() or "unknown")
