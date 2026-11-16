from types import SimpleNamespace

from roomscan.ml.hub import configure_cpu_inference


def test_cpu_inference_thread_count_is_bounded(monkeypatch):
    configured = []
    fake = SimpleNamespace(set_num_threads=configured.append)
    monkeypatch.setenv('ROOMSCAN_TORCH_THREADS', '64')
    monkeypatch.setattr('roomscan.ml.hub.os.cpu_count', lambda: 12)
    configure_cpu_inference(fake)
    assert configured == [12]


def test_invalid_thread_override_uses_safe_default(monkeypatch):
    configured = []
    fake = SimpleNamespace(set_num_threads=configured.append)
    monkeypatch.setenv('ROOMSCAN_TORCH_THREADS', 'many')
    monkeypatch.setattr('roomscan.ml.hub.os.cpu_count', lambda: 4)
    configure_cpu_inference(fake)
    assert configured == [4]
