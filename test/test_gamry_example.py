from argparse import Namespace

import pytest

from AFL.examples import test_gamry


def _base_args(**overrides):
    values = {
        'process_name': 'AFL_GamryDriver',
        'subprocess_timeout': 300.0,
        'initial_voltage': 0.0,
        'apex1_voltage': 0.2,
        'apex2_voltage': -0.5,
        'final_voltage': 0.0,
        'apex1_hold': 0.0,
        'apex2_hold': 0.0,
        'final_hold': 0.0,
        'scan_rate': 0.1,
        'step_size': 0.01,
        'cycles': 1,
        'scan_delay': 0.0,
        'current_range_mode': 'auto',
        'worker_path': '',
        'gamry_env_path': r'C:\\Users\\dnm33\\Documents\\GamryPython\\.venv',
        'instrument_name': 'PSTAT',
        'host': '127.0.0.1',
        'port': 5051,
        'tiled_uri': '',
        'tiled_api_key': '',
        'tiled_backup_path': '',
    }
    values.update(overrides)
    return Namespace(**values)


def test_build_data_backend_returns_none_without_tiled_uri(monkeypatch):
    args = _base_args()
    monkeypatch.setattr(test_gamry, '_read_global_tiled_config', lambda: {})

    assert test_gamry.build_data_backend(args) is None


def test_build_data_backend_requires_api_key_when_tiled_enabled(monkeypatch):
    args = _base_args(tiled_uri='http://localhost:8000', tiled_backup_path='json-backup')
    monkeypatch.setattr(test_gamry, '_read_global_tiled_config', lambda: {})

    with pytest.raises(ValueError, match='No Tiled API key found'):
        test_gamry.build_data_backend(args)


def test_build_data_backend_uses_global_config_fallback(monkeypatch):
    args = _base_args(tiled_backup_path='json-backup')
    monkeypatch.setattr(
        test_gamry,
        '_read_global_tiled_config',
        lambda: {'tiled_server': 'http://localhost:8000', 'tiled_api_key': 'config-key'},
    )
    monkeypatch.setattr(
        test_gamry,
        'DataTiled',
        lambda uri, api_key, backup_path: {
            'uri': uri,
            'api_key': api_key,
            'backup_path': backup_path,
        },
    )

    backend = test_gamry.build_data_backend(args)

    assert backend == {
        'uri': 'http://localhost:8000',
        'api_key': 'config-key',
        'backup_path': 'json-backup',
    }


def test_start_server_wires_data_backend(monkeypatch):
    args = _base_args(
        tiled_uri='http://localhost:8000',
        tiled_api_key='test-key',
        tiled_backup_path='json-backup',
    )
    driver = object()
    captured = {}

    class FakeServer:
        def __init__(self, name, data=None):
            captured['name'] = name
            captured['data'] = data

        def add_standard_routes(self):
            captured['routes'] = True

        def create_queue(self, queued_driver):
            captured['driver'] = queued_driver

        def run(self, host, port):
            captured['host'] = host
            captured['port'] = port

    monkeypatch.setattr(test_gamry, 'build_driver', lambda parsed_args: driver)
    monkeypatch.setattr(test_gamry, 'DataTiled', lambda uri, api_key, backup_path: {
        'uri': uri,
        'api_key': api_key,
        'backup_path': backup_path,
    })
    monkeypatch.setattr(test_gamry, 'APIServer', FakeServer)

    test_gamry.start_server(args)

    assert captured['name'] == 'gamry_demo'
    assert captured['data'] == {
        'uri': 'http://localhost:8000',
        'api_key': 'test-key',
        'backup_path': 'json-backup',
    }
    assert captured['driver'] is driver
    assert captured['host'] == '127.0.0.1'
    assert captured['port'] == 5051
