"""Markdown table of an `xcresulttool get test-results tests` JSON, for $GITHUB_STEP_SUMMARY."""
import json
import sys

try:
    d = json.load(open(sys.argv[1]))
except Exception as e:  # noqa: BLE001
    print(f"### Simulator tests\n\nCould not read the results: {e}")
    raise SystemExit(0)

MSG = ("Failure Message", "Skip Message", "Expected Failure Message")
rows = []


def walk(n, suite):
    if n.get("nodeType") == "Test Case":
        msgs = [c.get("name", "") for c in n.get("children") or [] if c.get("nodeType") in MSG]
        rows.append((suite, n.get("name"), n.get("result"), n.get("duration", ""), " / ".join(msgs)[:400]))
    for c in n.get("children") or []:
        walk(c, n.get("name") if n.get("nodeType") == "Test Suite" else suite)


for n in d.get("testNodes", []):
    walk(n, "")

icon = {"Passed": "✅", "Failed": "❌", "Skipped": "⏭️", "Expected Failure": "⚠️"}
counts = {}
for r in rows:
    counts[r[2]] = counts.get(r[2], 0) + 1
print("### Simulator tests\n")
print(", ".join(f"{icon.get(k, '')} {k}: {v}" for k, v in counts.items()) + "\n")
print("| | Test | Result | Time | Notes |\n|---|---|---|---|---|")
for suite, name, res, dur, msg in rows:
    print(f"| {icon.get(res, '')} | {suite}.{name} | {res} | {dur} | {msg.replace('|', '/').replace(chr(10), ' ')} |")
print("\nScreenshots: artifact `simulator-screenshots`.")
