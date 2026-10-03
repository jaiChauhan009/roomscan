"""Copy the PNG attachments exported by xcresulttool to <out>/<name>.png (names from manifest.json)."""
import json
import re
import shutil
import sys
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
try:
    manifest = json.loads((src / "manifest.json").read_text())
except Exception as e:  # noqa: BLE001
    print(f"no manifest: {e}")
    raise SystemExit(0)
for test in manifest:
    for a in test.get("attachments", []):
        f = a.get("exportedFileName", "")
        if not f.endswith(".png"):
            continue
        name = re.sub(r"_\d+_[0-9A-F-]{36}\.png$", "", a.get("suggestedHumanReadableName", f))
        name = re.sub(r"[^\w.-]+", "_", name).strip("_") or f
        shutil.copy(src / f, out / f"{name}.png")
        print(name)
