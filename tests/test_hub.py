"""Cached weights load without the network; missing ones are downloaded."""
import pytest

from roomscan.ml.hub import pretrained


class _Model:
    calls: list = []
    cached = True

    @classmethod
    def from_pretrained(cls, model_id, **kw):
        cls.calls.append(kw)
        if kw.get("local_files_only") and not cls.cached:
            raise OSError("not in the cache")
        return cls()


@pytest.mark.parametrize("cached", [True, False])
def test_cache_first_then_download(cached):
    _Model.calls, _Model.cached = [], cached
    assert isinstance(pretrained(_Model, "org/model", size=3), _Model)
    assert _Model.calls[0] == {"local_files_only": True, "size": 3}
    assert _Model.calls[1:] == ([] if cached else [{"size": 3}])
