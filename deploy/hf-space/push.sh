#!/usr/bin/env sh
# Push the server to a Hugging Face Space (Docker SDK).
#
# usage: deploy/hf-space/push.sh <hf-user>/<space-name>
#   needs: git, and an HF access token with write scope in $HF_TOKEN
#          (https://huggingface.co/settings/tokens)
#
# Builds the Space repository in a temporary directory: only what the image needs
# (pyproject.toml, uv.lock, src/, scripts/, server/, .dockerignore), with server/Dockerfile
# as ./Dockerfile and deploy/hf-space/README.md (the Space's YAML front matter) as ./README.md.
# Each push is one fresh commit, force-pushed: the Space repo holds no history of its own.
set -eu

SPACE="${1:?usage: $0 <hf-user>/<space-name>}"
: "${HF_TOKEN:?set HF_TOKEN to a Hugging Face token with write access}"
HF_USER="${SPACE%%/*}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REV="$(git -C "$ROOT" rev-parse --short HEAD)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# tracked files only (no .venv, caches, local data)
git -C "$ROOT" archive HEAD pyproject.toml uv.lock .dockerignore src scripts server | tar -x -C "$TMP"
cp "$ROOT/server/Dockerfile" "$TMP/Dockerfile"
cp "$ROOT/deploy/hf-space/README.md" "$TMP/README.md"

cd "$TMP"
git init -q -b main
git add -A
git -c user.name="roomscan deploy" -c user.email="deploy@roomscan.invalid" \
    commit -q -m "roomscan server from $REV"
git push --force "https://$HF_USER:$HF_TOKEN@huggingface.co/spaces/$SPACE" main
echo "pushed $REV to https://huggingface.co/spaces/$SPACE (build log: Space page > Logs)"
