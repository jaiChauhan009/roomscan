import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest
import yaml

spec = importlib.util.spec_from_file_location('device_validation', Path(__file__).parents[1] / 'scripts/prepare_device_validation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def capture(path, format='roomscan.roomplan/1'):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('capture.json', json.dumps({'format': format, 'rooms': [
            {'name': 'Living room', 'walls': [{'length': 999}], 'reference_measurements': []}]}))
    return path


def test_original_is_preserved_and_estimates_never_become_truth(tmp_path):
    source = capture(tmp_path/'scan.zip')
    original = source.read_bytes()
    folder = module.prepare(source, tmp_path/'dataset', 'flat-a', 'development')
    assert source.read_bytes() == original == (folder/'capture.roomscan.zip').read_bytes()
    manifest = json.loads((folder/'experiment.json').read_text())
    assert manifest['capture_sha256'] == hashlib.sha256(original).hexdigest()
    assert manifest['truth_complete'] is False
    truth = yaml.safe_load((folder/'ground_truth.yaml').read_text())
    assert truth['rooms'][0]['walls'] == []
    assert truth['source'] is None


def test_property_cannot_cross_the_holdout_boundary(tmp_path):
    source = capture(tmp_path/'scan.zip')
    module.prepare(source, tmp_path/'dataset', 'flat-a', 'development')
    with pytest.raises(ValueError, match='other split'):
        module.prepare(source, tmp_path/'dataset', 'flat-a', 'holdout')


def test_stray_scans_are_not_misrepresented_as_native_captures(tmp_path):
    source = capture(tmp_path/'scan.zip', 'stray')
    with pytest.raises(ValueError, match='native Roomscan'):
        module.prepare(source, tmp_path/'dataset', 'flat-a', 'holdout')
    assert not (tmp_path/'dataset').exists()
