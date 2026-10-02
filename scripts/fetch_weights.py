"""Download the pretrained models into the local Hugging Face cache, then check that the
pipeline can load them with the network switched off.

usage: uv run python scripts/fetch_weights.py      (once, after `uv sync --extra ml`)

  depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf   Apache-2.0   monocular metric depth (video, photo tiers)
  zju-community/efficientloftr                              Apache-2.0   image matching (photo-tier stitching)
  openai/clip-vit-base-patch32                              MIT          zero-shot damage classification (all tiers)

Each model is loaded the way the pipeline loads it (transformers `from_pretrained` with the
model ids from roomscan), so every file the pipeline asks for ends up in the cache. A list
of file patterns is not enough: openai/clip-vit-base-patch32 publishes its weights only as
pytorch_model.bin, and on first use transformers also fetches the hub's converted
model.safetensors copy in a background thread. Both are fetched here instead of during the
first capture run.

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

# The ids the pipeline uses. Importing these modules also applies any hub settings they make.
from roomscan.damage.detect import CLIP_ID  # noqa: E402
from roomscan.ml.depth import MODEL_ID as DEPTH_ID  # noqa: E402
from roomscan.ml.matching import MODEL_ID as MATCH_ID  # noqa: E402

MODELS = [DEPTH_ID, MATCH_ID, CLIP_ID]


def load_all(verbose: bool = True) -> None:
    try:
        from transformers import (AutoImageProcessor, AutoModelForDepthEstimation,
                                  AutoModelForKeypointMatching, CLIPModel, CLIPProcessor)
    except ImportError:
        sys.exit("transformers is not installed: run `uv sync --extra ml` first")
    loaders = {
        DEPTH_ID: lambda: (AutoImageProcessor.from_pretrained(DEPTH_ID),
                           AutoModelForDepthEstimation.from_pretrained(DEPTH_ID)),
        MATCH_ID: lambda: (AutoImageProcessor.from_pretrained(MATCH_ID),
                           AutoModelForKeypointMatching.from_pretrained(MATCH_ID)),
        CLIP_ID: lambda: (CLIPModel.from_pretrained(CLIP_ID), CLIPProcessor.from_pretrained(CLIP_ID)),
    }
    for m in MODELS:
        if verbose:
            print("fetching", m, flush=True)
        loaders[m]()
    # transformers' safetensors conversion runs in a background thread; wait for its download
    for t in threading.enumerate():
        if t.name.startswith("Thread-auto_conversion"):
            t.join()


def cache_size(model_id: str) -> int:
    from huggingface_hub import constants

    d = Path(constants.HF_HUB_CACHE) / ("models--" + model_id.replace("/", "--"))
    # count real files only: with symlinks enabled the snapshot entries point into blobs/
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file() and not f.is_symlink())


if __name__ == "__main__":
    if "--offline-check" in sys.argv:
        load_all(verbose=False)
        sys.exit(0)
    t0 = time.time()
    load_all()
    from huggingface_hub import constants

    print(f"cache {constants.HF_HUB_CACHE}:")
    for m in MODELS:
        print(f"  {cache_size(m) / 1e6:7.0f} MB  {m}")
    print(f"total {sum(cache_size(m) for m in MODELS) / 1e9:.2f} GB in {time.time() - t0:.0f} s; "
          "checking that the models load offline ...", flush=True)
    r = subprocess.run([sys.executable, __file__, "--offline-check"],
                       env={**os.environ, "HF_HUB_OFFLINE": "1"})
    if r.returncode != 0:
        sys.exit("offline load failed: some model files are missing from the cache (see above)")
    print("all weights cached; the pipeline can now run without network access")
