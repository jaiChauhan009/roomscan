"""User-facing review reasons. These heuristics never certify measurement accuracy."""
from __future__ import annotations


def review_output(output) -> None:
    meta = output.capture.meta
    tier = output.capture.tier
    issues = []

    def add(code, message):
        issues.append({'code': code, 'message': message})

    if not output.rooms:
        add('no_rooms', 'No complete rooms were reconstructed. Retake the capture.')
    if tier in ('photo', 'video'):
        scale = meta.get('scale') or {}
        sources = set((scale.get('rooms') or {}).values()) or {scale.get('source', 'model')}
        if not sources <= {'marker', 'known_sizes'}:
            add('unverified_scale', 'Some dimensions rely on estimated scale. Add a measured reference length or a visible scale marker.')
    if tier == 'video':
        candidates, tracked = meta.get('n_keyframes'), meta.get('n_tracked')
        if isinstance(candidates, (int, float)) and candidates > 0 and isinstance(tracked, (int, float)):
            if tracked / candidates < .8:
                add('partial_tracking', 'Tracking retained only part of this walkthrough. The plan may omit rooms; retake the missing sections slowly.')
    walls = [wall for room in output.rooms for wall in room.walls]
    if any(w.evidence_coverage < .5 for w in walls):
        add('weak_wall_evidence', 'Some wall boundaries have limited surface evidence. Check their positions and corners.')
    if any(w.length.value and w.length.ci90 and (w.length.ci90[1] - w.length.ci90[0]) / (2 * w.length.value) > .1
           for w in walls):
        add('wide_wall_intervals', 'Some wall lengths have uncertainty exceeding 10%. Measure those walls independently before using their dimensions.')
    if any(room.ceiling_height.value is None for room in output.rooms):
        add('ceiling_unobserved', 'Some ceilings were not observed. Include the wall tops and ceilings in a new scan.')
    if meta.get('outline_repairs'):
        add('outline_adjusted', 'Some boundaries were adjusted using upper-wall evidence. Review those room outlines against the property.')
    meta['quality_review'] = {'status': 'needs_review' if issues else 'unvalidated',
                              'accuracy_verified': False, 'issues': issues,
                              'note': 'Processing completion and absence of warnings do not verify measurement accuracy.'}
