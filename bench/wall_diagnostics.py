"""Explain wall-score failures without changing the geometry or the benchmark score.

python bench/wall_diagnostics.py RESULT TRUTH --out REPORT.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from evaluate import evaluate, pred_walls


def diagnose(pred: dict, truth: dict) -> dict:
    score = evaluate(pred, truth)
    by_id = {r['id']: r for r in pred['rooms']}
    rooms = []
    for room in truth['rooms']:
        pid = score['room_mapping'][room['name']]
        if pid is None:
            rooms.append({'room': room['name'], 'status': 'missing_room'})
            continue
        p = by_id[pid]
        walls = pred_walls(p)
        compared = [w for w in score['walls'] if w['room'] == room['name']]
        paired = {w['pred_id'] for w in compared if 'pred_id' in w}
        # Explicitly expose fragments that the length-only matcher did not score.
        unmatched = [w['id'] for w in walls if w['id'] not in paired]
        failures = sorted((w for w in compared if not w['ok']),
                          key=lambda w: abs(w.get('err') or 0), reverse=True)
        reasons = []
        if len(walls) != len(room.get('walls', [])):
            reasons.append('wall_count_mismatch: inspect furniture notches, real alcoves and missing walls')
        if unmatched:
            reasons.append('unpaired_predicted_walls: existing length scores omit these segments')
        if any(abs(w.get('err') or 0) > .2 for w in compared):
            reasons.append('large_length_error: verify wall correspondence before attributing sensor error')
        if 'polygon' in p:
            from shapely.geometry import Polygon
            polygon = Polygon(p['polygon'])
            if not polygon.is_valid:
                reasons.append('invalid_outline')
        rooms.append({'room': room['name'], 'predicted_room': pid,
                      'reference_wall_count': len(room.get('walls', [])),
                      'predicted_wall_count': len(walls), 'all_outline_segments': len(p['walls']),
                      'unpaired_predicted_wall_ids': unmatched, 'failed_walls': failures,
                      'review_reasons': reasons})
    return {'capture': pred['capture']['id'], 'reference_source': truth.get('source'),
            'note': 'Diagnostics do not repair correspondence or replace the original benchmark score.',
            'gates': score['gates'], 'rooms': rooms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    parser.add_argument('truth', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    report = diagnose(json.loads(args.result.read_text()), yaml.safe_load(args.truth.read_text()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
