import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'bench'))
from wall_diagnostics import diagnose
from evaluate import evaluate
from test_head_to_head import GT, OURS, _m


def test_fragmented_outline_exposes_segments_omitted_by_length_score():
    pred = copy.deepcopy(OURS)
    for i, room in enumerate(pred['rooms']):
        x = i * 10
        room['polygon'] = [[x, 0], [x + 4, 0], [x + 4, 3], [x, 3]]
    pred['rooms'][0]['walls'].append({'id': 'furniture_fragment', 'length': _m(.8)})
    report = diagnose(pred, GT)
    living = next(r for r in report['rooms'] if r['room'] == 'living')
    assert living['reference_wall_count'] == 4
    assert living['predicted_wall_count'] == 5
    assert len(living['unpaired_predicted_wall_ids']) == 1
    assert any('wall_count_mismatch' in reason for reason in living['review_reasons'])
    assert report['gates'] == evaluate(pred, GT)['gates']


def test_missing_room_is_explicit():
    pred = copy.deepcopy(OURS)
    pred['rooms'] = []
    report = diagnose(pred, GT)
    assert all(r['status'] == 'missing_room' for r in report['rooms'])
