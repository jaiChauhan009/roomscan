"""Print `udid=<id>` of an available iPhone simulator (iPhone 16 preferred) and boot it."""
import json
import subprocess
import sys

d = json.loads(subprocess.check_output(["xcrun", "simctl", "list", "devices", "available", "-j"]))["devices"]
c = [(rt, x) for rt, xs in d.items() if "iOS" in rt for x in xs if x["name"].startswith("iPhone")]
pref = [p for p in c if p[1]["name"] == "iPhone 16"] or [p for p in c if "Pro" not in p[1]["name"]] or c
pref.sort(key=lambda p: p[0])
rt, dev = pref[-1]
print(f"simulator: {dev['name']} ({rt})", file=sys.stderr)
subprocess.run(["xcrun", "simctl", "boot", dev["udid"]])
print(f"udid={dev['udid']}")
