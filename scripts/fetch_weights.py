"""Download the pretrained models into the local Hugging Face cache, then check that the
pipeline can load them with the network switched off.

usage:
  uv run python scripts/fetch_weights.py [--tier lidar|video|photo|all]   (after `uv sync --extra ml`)
  uv run --no-project --with huggingface-hub==1.33.0 python scripts/fetch_weights.py --download-only [--tier ...]
      (needs no project environment, so it can run while `uv sync --extra ml` is still
      downloading; run the first line afterwards, it then only checks the cache)

  model                                                     licence      tiers
  depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf   Apache-2.0   video, photo   (monocular metric depth)
  zju-community/efficientloftr                              Apache-2.0   photo          (image matching for stitching)
  openai/clip-vit-base-patch32                              MIT          all tiers      (zero-shot damage classification)

--tier (default all) fetches only what that tier loads: LiDAR needs CLIP only.

Each model is loaded the way the pipeline loads it (transformers `from_pretrained` with the
model ids from roomscan), so every file the pipeline asks for ends up in the cache. The
--download-only prefill uses file patterns (FILES below) and is only a head start: the
loading step that follows fetches anything it misses. openai/clip-vit-base-patch32
publishes its weights only as pytorch_model.bin. transformers loads that file and, unless
DISABLE_SAFETENSORS_CONVERSION is 1, also downloads the hub's converted model.safetensors
copy (605 MB) in a background thread, a copy it never loads. roomscan and this script set
the variable to 1 when it is unset, so that copy is not fetched; if you set it to 0
yourself, the copy is fetched here instead of during the first capture run.

Cache: the Hugging Face default (~/.cache/huggingface/hub, or $HF_HOME/hub).
No weights are stored in the repository.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
# as in roomscan.damage.detect: skip the unused safetensors copy of CLIP's pytorch_model.bin (605 MB)
os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "1")

# The ids the pipeline uses, repeated here so that --download-only runs without roomscan
# installed; check_ids() (run before every load, and by tests/test_fetch_weights.py)
# compares them with roomscan's own constants.
DEPTH_ID = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
MATCH_ID = "zju-community/efficientloftr"
CLIP_ID = "openai/clip-vit-base-patch32"

MODELS = [DEPTH_ID, MATCH_ID, CLIP_ID]
TIERS = {
    "lidar": [CLIP_ID],
    "video": [DEPTH_ID, CLIP_ID],
    "photo": [DEPTH_ID, MATCH_ID, CLIP_ID],
    "all": MODELS,
}
# files the pipeline loads, for the --download-only prefill
FILES = {
    DEPTH_ID: ["*.json", "model.safetensors"],
    MATCH_ID: ["*.json", "model.safetensors"],
    CLIP_ID: ["*.json", "*.txt", "pytorch_model.bin"],
}


def models_for(tier: str) -> list[str]:
    if tier not in TIERS:
        raise SystemExit(f"unknown tier {tier!r}: choose one of {', '.join(TIERS)}")
    return TIERS[tier]


def check_ids() -> None:
    """Fail if the ids above differ from the ones the pipeline loads."""
    from roomscan.damage.detect import CLIP_ID as clip
    from roomscan.ml.depth import MODEL_ID as depth
    from roomscan.ml.matching import MODEL_ID as match

    if (depth, match, clip) != (DEPTH_ID, MATCH_ID, CLIP_ID):
        raise SystemExit(f"scripts/fetch_weights.py model ids are out of date: roomscan uses "
                         f"{depth}, {match}, {clip}")


def download_only(models: list[str]) -> None:
    from huggingface_hub import snapshot_download

    for m in models:
        print("prefetching", m, flush=True)
        snapshot_download(m, allow_patterns=FILES[m])


def load_all(models: list[str] = MODELS, verbose: bool = True) -> None:
    try:
        from transformers import (AutoImageProcessor, AutoModelForDepthEstimation,
                                  AutoModelForKeypointMatching, CLIPModel, CLIPProcessor)
    except ImportError:
        sys.exit("transformers is not installed: run `uv sync --extra ml` first")
    check_ids()
    loaders = {
        DEPTH_ID: lambda: (AutoImageProcessor.from_pretrained(DEPTH_ID),
                           AutoModelForDepthEstimation.from_pretrained(DEPTH_ID)),
        MATCH_ID: lambda: (AutoImageProcessor.from_pretrained(MATCH_ID),
                           AutoModelForKeypointMatching.from_pretrained(MATCH_ID)),
        CLIP_ID: lambda: (CLIPModel.from_pretrained(CLIP_ID), CLIPProcessor.from_pretrained(CLIP_ID)),
    }
    for m in models:
        if verbose:
            print("fetching", m, flush=True)
        loaders[m]()
    # with DISABLE_SAFETENSORS_CONVERSION=0 the conversion download runs in a background thread: wait for it
    for t in threading.enumerate():
        if t.name.startswith("Thread-auto_conversion"):
            t.join()


def cache_size(model_id: str) -> int:
    from huggingface_hub import constants

    d = Path(constants.HF_HUB_CACHE) / ("models--" + model_id.replace("/", "--"))
    # count real files only: with symlinks enabled the snapshot entries point into blobs/
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file() and not f.is_symlink())


def parse_tier(argv: list[str]) -> str:
    for i, a in enumerate(argv):
        if a == "--tier" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--tier="):
            return a.split("=", 1)[1]
    return "all"


if __name__ == "__main__":
    tier = parse_tier(sys.argv[1:])
    models = models_for(tier)
    if "--offline-check" in sys.argv:
        load_all(models, verbose=False)
        sys.exit(0)
    t0 = time.time()
    if "--download-only" in sys.argv:
        download_only(models)
        print(f"prefetched {tier} weights in {time.time() - t0:.0f} s; after `uv sync --extra ml`, run "
              f"`uv run python scripts/fetch_weights.py --tier {tier}` to check them")
        sys.exit(0)
    load_all(models)
    from huggingface_hub import constants

    print(f"cache {constants.HF_HUB_CACHE}:")
    for m in models:
        print(f"  {cache_size(m) / 1e6:7.0f} MB  {m}")
    print(f"total {sum(cache_size(m) for m in models) / 1e9:.2f} GB in {time.time() - t0:.0f} s; "
          "checking that the models load offline ...", flush=True)
    r = subprocess.run([sys.executable, __file__, "--offline-check", "--tier", tier],
                       env={**os.environ, "HF_HUB_OFFLINE": "1"})
    if r.returncode != 0:
        sys.exit("offline load failed: some model files are missing from the cache (see above)")
    print(f"all {tier} weights cached; the pipeline can now run without network access"
          + ("" if tier == "all" else " for that tier"))
