from pathlib import Path

import xarray as xr

from AFL.automation.instrument.gamry import GamryDriver


class _PanelTestGamryDriver(GamryDriver):
    def __init__(self):
        self._test_instruments = ['PSTAT', 'PSTAT-2']
        super().__init__(
            gamry_env_path=r"C:\\fake\\env",
            instrument_name="PSTAT",
            overrides={
                'worker_path': r"C:\\fake\\worker.py",
                'service_host': '127.0.0.1',
                'service_port': 5059,
            },
        )

    def _is_port_in_use(self, host, port):
        return False

    def _bridge_ready(self):
        return False

    def listInstruments(self):
        return {'status': 'ok', 'result': {'instruments': list(self._test_instruments)}}

    def validateConnection(self):
        return {
            'status': 'ok',
            'result': {
                'instrument_name': self.config['instrument_name'],
                'serial_number': 'TEST-123',
            },
        }

    def collectCV(self, **kwargs):
        dataset = xr.Dataset(
            data_vars={
                'potential': ('point', [0.1, 0.2, 0.3]),
                'current': ('point', [1.0, 1.5, 1.2]),
                'time': ('point', [0.0, 1.0, 2.0]),
            },
            coords={'point': [0, 1, 2]},
        )
        dataset.attrs['instrument_name'] = self.config['instrument_name']
        dataset.attrs['point_count'] = 3
        dataset.attrs['parameters'] = {'scan_rate': kwargs.get('scan_rate', self.config['scan_rate'])}
        dataset.attrs['measurement_type'] = 'cyclic_voltammetry'
        dataset.attrs['x_key'] = 'potential'
        dataset.attrs['y_key'] = 'current'
        return dataset

    def runMeasurement(self, measurement_mode=None, instrument_name=None, return_data=False, **kwargs):
        mode = measurement_mode or self.config['measurement_mode']
        if instrument_name is not None:
            self.config['instrument_name'] = instrument_name
        if mode == 'ca':
            export_path = Path.home() / 'ca_panel_export.txt'
            export_path.write_text(
                'time_s\tvoltage_v\tcurrent_a\n'
                '0\t0.5\t1.0\n'
                '0.5\t0.5\t0.8\n'
                '1.0\t0.0\t0.6\n',
                encoding='utf-8',
            )
            dataset = xr.Dataset(
                data_vars={
                    'time': ('point', [9.0, 9.5, 10.0]),
                    'current': ('point', [8.0, 8.1, 8.2]),
                    'potential': ('point', [7.0, 7.1, 7.2]),
                },
                coords={'point': [0, 1, 2]},
            )
            dataset.attrs['measurement_type'] = 'chronoamperometry'
            dataset.attrs['x_key'] = 'time'
            dataset.attrs['y_key'] = 'current'
            dataset.attrs['parameters'] = {'text_export_path': str(export_path)}
        elif mode == 'dpv':
            export_path = Path.home() / 'dpv_panel_export.txt'
            export_path.write_text(
                'time_s\tvoltage_v\tcurrent_a\n'
                '0\t-1.0\t0.10\n'
                '0.2\t-1.0\t0.11\n'
                '0.39\t-1.0\t0.12\n'
                '0.49\t-1.0\t0.13\n'
                '0.5\t-0.975\t0.18\n'
                '0.7\t-0.995\t0.14\n'
                '0.89\t-0.995\t0.15\n'
                '0.99\t-0.97\t0.22\n',
                encoding='utf-8',
            )
            diff_export_path = Path.home() / 'dpv_panel_export_diff.txt'
            diff_export_path.write_text(
                'voltage_v\tdiff_current_a\n'
                '-1.0\t0.05\n'
                '-0.995\t0.07\n',
                encoding='utf-8',
            )
            dataset = xr.Dataset(
                data_vars={
                    'potential': ('point', [9.0, 9.1, 9.2]),
                    'current': ('point', [8.0, 8.1, 8.2]),
                    'time': ('point', [7.0, 7.1, 7.2]),
                },
                coords={'point': [0, 1, 2]},
            )
            dataset.attrs['measurement_type'] = 'differential_pulse_voltammetry'
            dataset.attrs['x_key'] = 'potential'
            dataset.attrs['y_key'] = 'current'
            dataset.attrs['parameters'] = {
                'text_export_path': str(export_path),
                'dpv_diff_export_path': str(diff_export_path),
            }
        elif mode == 'sine':
            export_path = Path.home() / 'sine_panel_export.txt'
            export_path.write_text(
                'time_s\tvoltage_v\tcurrent_a\n'
                '0\t0.0\t0.01\n'
                '0.1\t0.05\t0.02\n'
                '0.2\t0.0\t0.01\n',
                encoding='utf-8',
            )
            dataset = xr.Dataset(
                data_vars={
                    'time': ('point', [5.0, 5.1, 5.2]),
                    'current': ('point', [4.0, 4.1, 4.2]),
                    'potential': ('point', [3.0, 3.1, 3.2]),
                },
                coords={'point': [0, 1, 2]},
            )
            dataset.attrs['measurement_type'] = 'sine_wave'
            dataset.attrs['x_key'] = 'time'
            dataset.attrs['y_key'] = 'current'
            dataset.attrs['parameters'] = {'text_export_path': str(export_path)}
        else:
            export_path = Path.home() / 'cv_panel_export.txt'
            export_path.write_text(
                'time_s\tvoltage_v\tcurrent_a\n'
                '0\t0.1\t1.0\n'
                '1.0\t0.2\t1.5\n'
                '2.0\t0.3\t1.2\n',
                encoding='utf-8',
            )
            dataset = self.collectCV(**kwargs)
            dataset.attrs['parameters'] = {'text_export_path': str(export_path)}
        dataset.attrs['instrument_name'] = self.config['instrument_name']
        dataset.attrs['point_count'] = 3
        self._last_cv_dataset = dataset
        return dataset


def test_get_panel_state_returns_config_and_service_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    driver = _PanelTestGamryDriver()

    state = driver.getPanelState()

    assert state['status'] == 'ok'
    assert state['config']['instrument_name'] == 'PSTAT'
    assert state['service']['bridge_ready'] is False
    assert state['last_result'] is None
    assert 'initial_voltage' in state['quickbar']


def test_update_panel_config_persists_numeric_values(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    driver = _PanelTestGamryDriver()

    result = driver.updatePanelConfig(
        instrument_name='PSTAT-2',
        initial_voltage=0.25,
        scan_rate=0.5,
        cycles=3,
        current_range_mode='manual',
    )

    assert result['status'] == 'ok'
    assert driver.config['instrument_name'] == 'PSTAT-2'
    assert driver.config['initial_voltage'] == 0.25
    assert driver.config['scan_rate'] == 0.5
    assert driver.config['cycles'] == 3
    assert driver.config['current_range_mode'] == 'manual'


def test_update_panel_config_persists_mode_specific_values(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    driver = _PanelTestGamryDriver()

    result = driver.updatePanelConfig(
        measurement_mode='sine',
        sine_dc_offset=0.1,
        sine_amplitude=0.02,
        sine_frequency=25.0,
        sine_acq_frequency=1000.0,
        sine_total_time=4.0,
    )

    assert result['status'] == 'ok'
    assert driver.config['measurement_mode'] == 'sine'
    assert driver.config['sine_dc_offset'] == 0.1
    assert driver.config['sine_amplitude'] == 0.02
    assert driver.config['sine_frequency'] == 25.0
    assert driver.config['sine_acq_frequency'] == 1000.0
    assert driver.config['sine_total_time'] == 4.0


def test_update_panel_config_persists_dpv_values(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    driver = _PanelTestGamryDriver()

    result = driver.updatePanelConfig(
        measurement_mode='dpv',
        dpv_initial_voltage=-1.0,
        dpv_final_voltage=0.0,
        dpv_step_size=0.005,
        dpv_pulse_size=0.025,
        dpv_sample_period=0.5,
        dpv_pulse_time=0.1,
        dpv_noise_rejection=True,
        dpv_irange_mode='fixed',
        dpv_max_current=0.3,
    )

    assert result['status'] == 'ok'
    assert driver.config['measurement_mode'] == 'dpv'
    assert driver.config['dpv_initial_voltage'] == -1.0
    assert driver.config['dpv_final_voltage'] == 0.0
    assert driver.config['dpv_step_size'] == 0.005
    assert driver.config['dpv_pulse_size'] == 0.025
    assert driver.config['dpv_sample_period'] == 0.5
    assert driver.config['dpv_noise_rejection'] is True
    assert driver.config['dpv_irange_mode'] == 'fixed'
    assert driver.config['dpv_max_current'] == 0.3


def test_connect_instrument_updates_selected_potentiostat(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    driver = _PanelTestGamryDriver()

    result = driver.connectInstrument('PSTAT-2')

    assert result['status'] == 'ok'
    assert driver.config['instrument_name'] == 'PSTAT-2'
    assert result['connection']['instrument_name'] == 'PSTAT-2'
    assert result['connection']['validation']['result']['serial_number'] == 'TEST-123'
    assert result['available_instruments']['instruments'] == ['PSTAT', 'PSTAT-2']
    assert result['service']['bridge_ready'] is False
    assert result['service']['bridge_usable'] is True


def test_run_cv_now_serializes_dataset_and_caches_last_result(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    driver = _PanelTestGamryDriver()

    result = driver.runCVNow(scan_rate=0.75)

    assert result['status'] == 'ok'
    assert result['result']['attrs']['instrument_name'] == 'PSTAT'
    assert result['result']['attrs']['point_count'] == 3
    assert result['result']['attrs']['text_export_name'] == 'cv_panel_export.txt'
    assert result['result']['attrs']['plot_source'] == 'text_export'
    assert result['result']['plot_data']['voltage_v'] == [0.1, 0.2, 0.3]
    assert result['result']['plot_data']['current_a'] == [1.0, 1.5, 1.2]
    assert driver.getPanelState()['last_result']['plot_data']['time_s'] == [0.0, 1.0, 2.0]


def test_run_measurement_now_serializes_generic_result(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    driver = _PanelTestGamryDriver()

    result = driver.runMeasurementNow(measurement_mode='ca')

    assert result['status'] == 'ok'
    assert result['result']['attrs']['measurement_type'] == 'chronoamperometry'
    assert result['result']['attrs']['text_export_name'] == 'ca_panel_export.txt'
    assert result['result']['attrs']['plot_source'] == 'text_export'
    assert result['result']['plot_data']['time_s'] == [0.0, 0.5, 1.0]
    assert result['result']['plot_data']['voltage_v'] == [0.5, 0.5, 0.0]
    assert result['result']['plot_data']['current_a'] == [1.0, 0.8, 0.6]


def test_run_measurement_now_serializes_dpv_result_from_text_export(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    driver = _PanelTestGamryDriver()

    result = driver.runMeasurementNow(measurement_mode='dpv')

    assert result['status'] == 'ok'
    assert result['result']['attrs']['measurement_type'] == 'differential_pulse_voltammetry'
    assert result['result']['attrs']['text_export_path'] == str(tmp_path / 'dpv_panel_export_diff.txt')
    assert result['result']['attrs']['text_export_name'] == 'dpv_panel_export_diff.txt'
    assert result['result']['attrs']['raw_text_export_path'] == str(tmp_path / 'dpv_panel_export.txt')
    assert result['result']['attrs']['raw_text_export_name'] == 'dpv_panel_export.txt'
    assert result['result']['attrs']['plot_source'] == 'text_export'
    assert result['result']['attrs']['plot_variant'] == 'dpv_differential'
    assert 'data' not in result['result']
    assert result['result']['plot_data']['voltage_v'] == [-1.0, -0.995]
    assert result['result']['plot_data']['diff_current_a'] == [0.05, 0.07]
    assert 'current_a' not in result['result']['plot_data']
    assert 'time_s' not in result['result']['plot_data']
    assert 'data' not in driver.getPanelState()['last_result']
    assert driver.getPanelState()['last_result']['plot_data']['voltage_v'] == [-1.0, -0.995]


def test_run_measurement_now_serializes_sine_result_from_text_export(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    driver = _PanelTestGamryDriver()

    result = driver.runMeasurementNow(measurement_mode='sine')

    assert result['status'] == 'ok'
    assert result['result']['attrs']['measurement_type'] == 'sine_wave'
    assert result['result']['attrs']['text_export_name'] == 'sine_panel_export.txt'
    assert result['result']['attrs']['plot_source'] == 'text_export'
    assert result['result']['plot_data']['time_s'] == [0.0, 0.1, 0.2]
    assert result['result']['plot_data']['voltage_v'] == [0.0, 0.05, 0.0]
    assert result['result']['plot_data']['current_a'] == [0.01, 0.02, 0.01]
