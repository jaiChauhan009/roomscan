#!/usr/bin/env python3
"""Download an iOS validation run and record source/artifact hashes without credentials."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import tempfile


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, timeout=120)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--repo', default='jaiChauhan009/roomscan')
    parser.add_argument('--output', type=Path, default=Path('runs/ios-validation'))
    args = parser.parse_args()
    run_id = str(args.run_id)
    run = json.loads(command('gh', 'run', 'view', run_id, '--repo', args.repo, '--json',
                             'databaseId,url,headSha,headBranch,status,conclusion,jobs,createdAt,updatedAt'))
    if run['status'] != 'completed':
        print(f"Run {run_id} is {run['status']}; collect after it completes.")
        return 2
    destination = args.output / run_id
    destination.mkdir(parents=True, exist_ok=True)
    artifacts = json.loads(command('gh', 'api', f'repos/{args.repo}/actions/runs/{run_id}/artifacts'))
    for artifact in artifacts['artifacts']:
        if not artifact['expired']:
            print(f"Downloading {artifact['name']}...", flush=True)
            # gh refuses to extract over existing files. Download into a fresh directory
            # so re-running collection after an interruption is safe and repeatable.
            with tempfile.TemporaryDirectory(prefix='roomscan-artifact-') as temporary:
                command('gh', 'run', 'download', run_id, '--repo', args.repo,
                        '--name', artifact['name'], '--dir', temporary)
                shutil.copytree(temporary, destination / artifact['name'], dirs_exist_ok=True)
    source_files = command('git', 'ls-tree', '-r', '--name-only', run['headSha'], '--',
                           'ios', '.github/workflows/ios-validation.yml').splitlines()
    inputs = {}
    for name in source_files:
        content = subprocess.check_output(['git', 'show', f"{run['headSha']}:{name}"])
        inputs[name] = hashlib.sha256(content).hexdigest()
    outputs = {}
    for path in sorted(destination.rglob('*')):
        if path.is_file() and path.name != 'manifest.json':
            outputs[str(path.relative_to(destination))] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'repository': args.repo,
                'run': run, 'artifacts': artifacts['artifacts'], 'source_sha256': inputs,
                'output_sha256': outputs,
                'scope': 'Unsigned device build, simulator unit tests and synthetic preview UI tests; no real LiDAR accuracy test.'}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f"Recorded {len(inputs)} source files and {len(outputs)} outputs at {destination}")
    return 0 if run['conclusion'] == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(main())
