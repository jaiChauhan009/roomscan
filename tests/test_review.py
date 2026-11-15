from types import SimpleNamespace as NS

from roomscan.review import review_output


def output(tier='video', meta=None, width=.02, coverage=.95):
    wall = NS(evidence_coverage=coverage, length=NS(value=3, ci90=[3-width, 3+width]))
    return NS(capture=NS(tier=tier, meta=meta or {}),
              rooms=[NS(walls=[wall], ceiling_height=NS(value=2.6))])


def test_lost_walkthrough_coverage_and_model_scale_are_visible():
    result = output(meta={'n_keyframes': 100, 'n_tracked': 25, 'scale': {'source': 'model'}})
    review_output(result)
    review = result.capture.meta['quality_review']
    assert {i['code'] for i in review['issues']} == {'partial_tracking', 'unverified_scale'}
    assert review['accuracy_verified'] is False


def test_a_marker_cannot_make_weak_geometry_verified():
    result = output('photo', {'scale': {'source': 'marker'}}, width=1, coverage=.1)
    review_output(result)
    codes = {i['code'] for i in result.capture.meta['quality_review']['issues']}
    assert codes == {'weak_wall_evidence', 'wide_wall_intervals'}


def test_clean_synthetic_geometry_is_still_not_accuracy_certified():
    result = output('roomplan')
    review_output(result)
    assert result.capture.meta['quality_review']['status'] == 'unvalidated'
    assert result.capture.meta['quality_review']['accuracy_verified'] is False
