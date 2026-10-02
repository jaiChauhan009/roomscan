"""Pretrained weights from the local Hugging Face cache, without asking the hub first.

scripts/fetch_weights.py puts every model in the cache at install time. Plain
from_pretrained still asks the hub about each file on every load: seconds of round trips,
an "unauthenticated requests" warning, and a failure point on a machine without internet
(the walk-in). A cached model loads offline; only a model missing from the cache is
downloaded.
"""
from __future__ import annotations


def pretrained(cls, model_id: str, **kwargs):
    try:
        return cls.from_pretrained(model_id, local_files_only=True, **kwargs)
    except OSError:  # not in the cache (fetch_weights.py not run): download it
        return cls.from_pretrained(model_id, **kwargs)
