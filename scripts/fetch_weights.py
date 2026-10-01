"""Download the pretrained models into the local Hugging Face cache (about 0.8 GB).

Run once after installing. After this the pipeline runs offline. Models and licences:

  depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf   Apache-2.0   monocular metric depth (video, photo tiers)
  zju-community/efficientloftr                              Apache-2.0   image matching (photo-tier stitching)
  openai/clip-vit-base-patch32                              MIT          zero-shot damage classification (all tiers)

No weights are stored in the repository.
"""
import os

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from huggingface_hub import snapshot_download  # noqa: E402

MODELS = [
    "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf",
    "zju-community/efficientloftr",
    "openai/clip-vit-base-patch32",
]

if __name__ == "__main__":
    for m in MODELS:
        print("fetching", m, flush=True)
        snapshot_download(m, allow_patterns=["*.json", "*.txt", "*.safetensors", "*.model"])
    print("all weights cached; the pipeline can now run without network access")
