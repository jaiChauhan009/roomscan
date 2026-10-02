"""scripts/fetch_weights.py: tier selection and the --download-only prefill, without network."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fetch_weights.py"
_spec = importlib.util.spec_from_file_location("fetch_weights", _PATH)
fw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fw)


def test_ids_match_the_pipeline():
    fw.check_ids()  # raises SystemExit if the script's ids drifted from roomscan's


def test_tier_selection():
    assert fw.models_for("lidar") == [fw.CLIP_ID]
    assert fw.models_for("video") == [fw.DEPTH_ID, fw.CLIP_ID]
    assert set(fw.models_for("photo")) == {fw.DEPTH_ID, fw.MATCH_ID, fw.CLIP_ID}
    assert fw.models_for("all") == fw.MODELS
    with pytest.raises(SystemExit):
        fw.models_for("drone")
    assert fw.parse_tier([]) == "all"
    assert fw.parse_tier(["--tier", "lidar"]) == "lidar"
    assert fw.parse_tier(["--download-only", "--tier=video"]) == "video"


def test_download_only_fetches_the_tier_models_only(monkeypatch):
    calls = []
    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = lambda repo, allow_patterns: calls.append((repo, allow_patterns))
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    fw.download_only(fw.models_for("lidar"))
    assert calls == [(fw.CLIP_ID, ["*.json", "*.txt", "pytorch_model.bin"])]
    # CLIP's weights are pytorch_model.bin only; its 605 MB safetensors conversion is never loaded
    assert not any("safetensors" in p for p in fw.FILES[fw.CLIP_ID])


def test_load_selects_the_tier_models(monkeypatch):
    loaded = []

    class Fake:
        def __init__(self, name):
            self.name = name

        def from_pretrained(self, model_id, **kw):
            loaded.append((self.name, model_id))

    tf = types.ModuleType("transformers")
    for n in ("AutoImageProcessor", "AutoModelForDepthEstimation", "AutoModelForKeypointMatching",
              "CLIPModel", "CLIPProcessor"):
        setattr(tf, n, Fake(n))
    monkeypatch.setitem(sys.modules, "transformers", tf)
    fw.load_all(fw.models_for("lidar"), verbose=False)
    assert {m for _, m in loaded} == {fw.CLIP_ID}
    loaded.clear()
    fw.load_all(fw.models_for("photo"), verbose=False)
    assert {m for _, m in loaded} == set(fw.MODELS)
