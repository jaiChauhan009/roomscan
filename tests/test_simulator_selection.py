import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('pick_simulator', Path(__file__).parents[1] / 'ios/ci/pick_simulator.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_family_and_runtime_selection():
    devices = {'iOS-9-3': [{'name': 'iPhone 16', 'udid': 'old'}],
               'iOS-18-4': [{'name': 'iPhone 16', 'udid': 'phone'}, {'name': 'iPad Pro', 'udid': 'pad'}]}
    assert module.choose_device(devices)[1]['udid'] == 'phone'
    assert module.choose_device(devices, 'ipad')[1]['udid'] == 'pad'
    with pytest.raises(RuntimeError, match='No available iPad'):
        module.choose_device({'iOS-18-4': [{'name': 'iPhone 16'}]}, 'ipad')
