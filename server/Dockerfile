# roomscan web API on a CPU host (Hugging Face Spaces "Docker" SDK, Railway, Render, Fly, ...).
# Build from the repository root:
#     docker build -f server/Dockerfile -t roomscan-server .
#     docker run -p 7860:7860 -v roomscan-data:/data roomscan-server
FROM python:3.11-slim

# open3d / opencv runtime libraries
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libegl1 libgomp1 libglib2.0-0 libusb-1.0-0 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    HF_HOME=/opt/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DISABLE_SAFETENSORS_CONVERSION=1 \
    PYTHONPATH=/app/src:/app \
    PATH=/app/.venv/bin:$PATH \
    DATA_DIR=/data

WORKDIR /app

# Dependencies from uv.lock (= `uv sync --extra ml --extra server`), except that torch comes
# from the CPU wheel index: the PyPI Linux wheel pulls ~4 GB of CUDA libraries a CPU host never uses.
COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --extra ml --extra server --no-emit-project --no-hashes -o /tmp/req.txt \
 && grep -vE '^(torch|triton|nvidia-|cuda-)' /tmp/req.txt > /tmp/req-cpu.txt \
 && TORCH=$(grep -E '^torch==' /tmp/req.txt | cut -d';' -f1 | tr -d ' ') \
 && uv venv /app/.venv \
 && uv pip install --python /app/.venv -r /tmp/req-cpu.txt \
 && uv pip install --python /app/.venv "$TORCH" --index-url https://download.pytorch.org/whl/cpu \
 && rm -rf /root/.cache/uv /tmp/req*.txt

COPY src ./src
COPY scripts ./scripts
COPY server ./server

# Model weights baked into the image, so it starts and runs offline.
RUN python scripts/fetch_weights.py && chmod -R a+rX /opt/hf
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

# Hugging Face Spaces runs the container as user 1000; /data is its persistent-storage mount.
RUN mkdir -p /data && chmod 777 /data

EXPOSE 7860
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "7860"]
