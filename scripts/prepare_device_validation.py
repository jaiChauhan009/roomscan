"""Preserve an original native scan and prepare an explicitly unfilled measurement sheet.

python scripts/prepare_device_validation.py SCAN.roomscan.zip --property-id flat-a \
    --split holdout --dataset runs/device-validation

Independent measurements must be supplied by a person; scanned dimensions are never used
as ground truth. A property cannot enter both development and holdout in the same dataset.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid
import zipfile

import yaml


def prepare(capture: Path, dataset: Path, property_id: str, split: str) -> Path:
    property_id = property_id.strip()
    if not property_id.strip() or split not in ('development', 'holdout'):
        raise ValueError('Supply a property id and a development/holdout split')
    with zipfile.ZipFile(capture) as archive:
        files = [f for f in archive.infolist() if f.filename.rsplit('/', 1)[-1] == 'capture.json'
                 and f.filename.count('/') <= 1 and '__MACOSX' not in f.filename]
        if len(files) != 1 or files[0].file_size > 32 * 1024 * 1024:
            raise ValueError('Expected one native capture.json, at most 32 MB, at the ZIP root or one folder below')
        data = json.loads(archive.read(files[0]).decode('utf-8-sig'))
    if not isinstance(data, dict) or data.get('format') != 'roomscan.roomplan/1' or not data.get('rooms'):
        raise ValueError('A native Roomscan RoomPlan export with at least one room is required')
    if not isinstance(data['rooms'], list) or not all(isinstance(room, dict) for room in data['rooms']):
        raise ValueError('Every captured room must be an object')
    for manifest in dataset.glob('*/experiment.json'):
        previous = json.loads(manifest.read_text())
        if previous['property_id'] == property_id and previous['split'] != split:
            raise ValueError('This property already belongs to the other split; do not leak rooms between development and holdout')
    with capture.open('rb') as original:
        digest = hashlib.file_digest(original, 'sha256').hexdigest()
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    destination = dataset / run_id
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(capture, destination / 'capture.roomscan.zip')
    with (destination / 'capture.roomscan.zip').open('rb') as copied:
        if hashlib.file_digest(copied, 'sha256').hexdigest() != digest:
            raise IOError('The copied capture checksum does not match the original')
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True,
                                         cwd=Path(__file__).resolve().parents[1]).strip()
    except subprocess.CalledProcessError:
        commit = None
    experiment = {'run_id': run_id, 'property_id': property_id, 'split': split,
                  'capture_sha256': digest, 'capture_file': 'capture.roomscan.zip',
                  'app_version': data.get('app_version'), 'device': data.get('device'),
                  'captured_at': data.get('captured_at'), 'source_commit_at_import': commit,
                  'truth_complete': False, 'reference_source': 'independent measurements not yet supplied'}
    (destination / 'experiment.json').write_text(json.dumps(experiment, indent=2))
    rooms = []
    references = []
    for i, room in enumerate(data['rooms']):
        if not isinstance(room, dict):
            raise ValueError('Every captured room must be an object')
        name = str(room.get('name') or f'Room {i+1}')
        # Do not copy the scanner's wall count or wall dimensions into truth.
        rooms.append({'name': name, 'walls': [], 'ceiling_height': None, 'openings': None})
        references.append({'room': name, 'references_declared_by_user': room.get('reference_measurements', [])})
    truth = {'capture': run_id, 'source': None, 'instrument': None,
             'measurement_uncertainty_m': None, 'measured_by': None, 'date': None,
             'footprint': None, 'rooms': rooms}
    (destination / 'ground_truth.yaml').write_text(
        '# UNFILLED: measure every physical wall independently in cyclic order, in metres.\n'
        '# Do not copy lengths or the number of segments from the scanner output.\n'
        '# Set source to tape or laser; fill instrument, uncertainty, operator and date.\n'
        + yaml.safe_dump(truth, sort_keys=False))
    (destination / 'recorded_references.json').write_text(json.dumps(references, indent=2))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--property-id', required=True)
    parser.add_argument('--split', choices=['development', 'holdout'], required=True)
    parser.add_argument('--dataset', type=Path, default=Path('runs/device-validation'))
    args = parser.parse_args()
    print(prepare(args.capture, args.dataset, args.property_id, args.split))


if __name__ == '__main__':
    main()
