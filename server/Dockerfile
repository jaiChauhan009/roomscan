# roomscan web API on a CPU host (Hugging Face Spaces "Docker" SDK, Railway, Render, Fly, ...).
# Build from the repository root:
#     docker build -f server/Dockerfile -t roomscan-server .
#     docker run -p 7860:7860 -v roomscan-data:/data roomscan-server
# (Hugging Face Spaces builds ./Dockerfile at the Space repo root: copy this file there, see
#  docs/deploy.md.) The image is ~3 GB: CPU torch, open3d, and ~0.8 GB of model weights.
FROM python:3.11-slim

# Runtime libraries: libgl1 / libegl1 / libgomp1 / libusb / libidn2 / libtbb for open3d, libglib2.0-0 for opencv
# (headless). pillow-heif ships manylinux wheels with libheif.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libegl1 libgomp1 libglib2.0-0 libusb-1.0-0 libidn2-0 libtbb12 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Hugging Face Spaces runs the container as uid 1000: give that user a home (a writable
# $HOME for matplotlib / torch caches) and own everything it writes to.
RUN useradd -m -u 1000 user

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    HOME=/home/user \
    MPLCONFIGDIR=/home/user/.config/matplotlib \
    HF_HOME=/opt/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DISABLE_SAFETENSORS_CONVERSION=1 \
    PYTHONPATH=/app/src:/app \
    PATH=/app/.venv/bin:$PATH \
    DATA_DIR=/data \
    ROOMSCAN_RESULT_TTL_DAYS=2 \
    PORT=7860

WORKDIR /app

# Dependencies from uv.lock (= `uv sync --extra ml --extra server`), except that torch comes
# from the CPU wheel index: the PyPI Linux wheel pulls ~4 GB of CUDA libraries a CPU host never
# uses (torch's cuda-* / nvidia-* / triton dependencies are dropped with it).
# The venv uses the image's Python 3.11 (pyproject sets python-preference = "managed", which
# would otherwise download a second interpreter).
COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --extra ml --extra server --no-emit-project --no-hashes -o /tmp/req.txt \
 && grep -vE '^(torch|triton|nvidia-|cuda-)' /tmp/req.txt > /tmp/req-cpu.txt \
 && TORCH=$(grep -E '^torch==' /tmp/req.txt | cut -d';' -f1 | tr -d ' ') \
 && test -n "$TORCH" \
 && uv venv --python /usr/local/bin/python3.11 /app/.venv \
 && uv pip install --python /app/.venv -r /tmp/req-cpu.txt \
 && uv pip install --python /app/.venv "$TORCH" --index-url https://download.pytorch.org/whl/cpu \
 && rm -rf /root/.cache/uv /tmp/req*.txt

# fail the build here, not at a user's first LiDAR run, if a native library is missing
RUN python -c "import open3d, open3d.pipelines.registration, cv2, torch, scipy, shapely, skimage"

COPY src ./src
COPY scripts ./scripts
COPY server ./server

# Model weights baked into the image (~0.8 GB), so it starts fast and runs offline.
RUN python scripts/fetch_weights.py && chmod -R a+rX /opt/hf
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

# DATA_DIR: Spaces mount paid persistent storage at /data; without it /data is plain container
# disk, wiped on every restart / rebuild (results then live at most until the Space restarts).
RUN mkdir -p /data /home/user/.config/matplotlib \
 && chown -R user:user /data /home/user

USER user

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '7860'), timeout=4)"

# One worker process: jobs and state live on one disk and one worker thread. $PORT lets
# Railway / Render / Fly pick the port (7860 on Spaces).
CMD ["sh", "-c", "exec uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips '*'"]
