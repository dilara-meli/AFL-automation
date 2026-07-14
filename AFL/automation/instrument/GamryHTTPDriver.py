from typing import Any, Dict, Optional

import xarray as xr

from AFL.automation.APIServer.Client import Client
from AFL.automation.APIServer.Driver import Driver


class GamryHTTPDriver(Driver):
    defaults = {}
    defaults['server_ip'] = '127.0.0.1'
    defaults['server_port'] = '5051'
    defaults['server_username'] = 'GamryHTTPDriver'
    defaults['measurement_mode'] = 'cv'
    defaults['instrument_name'] = 'PSTAT'
    defaults['initial_voltage'] = 0.0
    defaults['apex1_voltage'] = -0.2
    defaults['apex2_voltage'] = 0.5
    defaults['final_voltage'] = 0.0
    defaults['apex1_hold'] = 0.0
    defaults['apex2_hold'] = 0.0
    defaults['final_hold'] = 0.0
    defaults['scan_rate'] = 0.1
    defaults['step_size'] = 0.001
    defaults['cycles'] = 1
    defaults['scan_delay'] = 0.0
    defaults['current_range_mode'] = 'auto'
    defaults['ca_initial_voltage'] = 0.0
    defaults['ca_step1_voltage'] = 0.5
    defaults['ca_step2_voltage'] = 0.0
    defaults['ca_initial_time'] = 1.0
    defaults['ca_step1_time'] = 2.0
    defaults['ca_step2_time'] = 2.0
    defaults['ca_sample_time'] = 0.05
    defaults['ca_expected_max_v'] = 10.0
    defaults['sine_dc_offset'] = 0.0
    defaults['sine_amplitude'] = 0.05
    defaults['sine_frequency'] = 10.0
    defaults['sine_acq_frequency'] = 1000.0
    defaults['sine_total_time'] = 0.5
    defaults['sine_phase_offset'] = 0.0
    defaults['dpv_initial_voltage'] = -1.0
    defaults['dpv_final_voltage'] = 0.0
    defaults['dpv_step_size'] = 0.005
    defaults['dpv_pulse_size'] = 0.025
    defaults['dpv_sample_period'] = 0.5
    defaults['dpv_pulse_time'] = 0.1
    defaults['dpv_noise_rejection'] = True
    defaults['dpv_irange_mode'] = 'fixed'
    defaults['dpv_max_current'] = 0.0003

    def __init__(self, overrides=None):
        self.app = None
        self._client = None
        Driver.__init__(self, name='GamryHTTPDriver', defaults=self.gather_defaults(), overrides=overrides)
        self.useful_links['Remote Gamry Driver'] = self._server_url()

    def status(self):
        return [
            f"server_url={self._server_url()}",
            f"instrument_name={self.config['instrument_name']}",
            f"measurement_mode={self.config['measurement_mode']}",
        ]

    def _server_url(self) -> str:
        return f"http://{self.config['server_ip']}:{self.config['server_port']}"

    def _get_client(self) -> Client:
        if self._client is None:
            client = Client(
                ip=str(self.config['server_ip']),
                port=str(self.config['server_port']),
                interactive=False,
            )
            username = str(self.config.get('server_username', '')).strip()
            if username:
                client.login(username)
            self._client = client
        return self._client

    def _reset_client(self) -> None:
        self._client = None

    def _coerce_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'on'}
        return bool(value)

    def _measurement_parameters(self, mode: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
        if mode == 'cv':
            return {
                'initial_voltage': float(self.config['initial_voltage'] if overrides.get('initial_voltage') is None else overrides['initial_voltage']),
                'apex1_voltage': float(self.config['apex1_voltage'] if overrides.get('apex1_voltage') is None else overrides['apex1_voltage']),
                'apex2_voltage': float(self.config['apex2_voltage'] if overrides.get('apex2_voltage') is None else overrides['apex2_voltage']),
                'final_voltage': float(self.config['final_voltage'] if overrides.get('final_voltage') is None else overrides['final_voltage']),
                'apex1_hold': float(self.config['apex1_hold'] if overrides.get('apex1_hold') is None else overrides['apex1_hold']),
                'apex2_hold': float(self.config['apex2_hold'] if overrides.get('apex2_hold') is None else overrides['apex2_hold']),
                'final_hold': float(self.config['final_hold'] if overrides.get('final_hold') is None else overrides['final_hold']),
                'scan_rate': float(self.config['scan_rate'] if overrides.get('scan_rate') is None else overrides['scan_rate']),
                'step_size': float(self.config['step_size'] if overrides.get('step_size') is None else overrides['step_size']),
                'cycles': int(self.config['cycles'] if overrides.get('cycles') is None else overrides['cycles']),
                'scan_delay': float(self.config['scan_delay'] if overrides.get('scan_delay') is None else overrides['scan_delay']),
                'current_range_mode': str(self.config['current_range_mode'] if overrides.get('current_range_mode') is None else overrides['current_range_mode']),
            }
        if mode == 'ca':
            return {
                'ca_initial_voltage': float(self.config['ca_initial_voltage'] if overrides.get('ca_initial_voltage') is None else overrides['ca_initial_voltage']),
                'ca_step1_voltage': float(self.config['ca_step1_voltage'] if overrides.get('ca_step1_voltage') is None else overrides['ca_step1_voltage']),
                'ca_step2_voltage': float(self.config['ca_step2_voltage'] if overrides.get('ca_step2_voltage') is None else overrides['ca_step2_voltage']),
                'ca_initial_time': float(self.config['ca_initial_time'] if overrides.get('ca_initial_time') is None else overrides['ca_initial_time']),
                'ca_step1_time': float(self.config['ca_step1_time'] if overrides.get('ca_step1_time') is None else overrides['ca_step1_time']),
                'ca_step2_time': float(self.config['ca_step2_time'] if overrides.get('ca_step2_time') is None else overrides['ca_step2_time']),
                'ca_sample_time': float(self.config['ca_sample_time'] if overrides.get('ca_sample_time') is None else overrides['ca_sample_time']),
                'ca_expected_max_v': float(self.config['ca_expected_max_v'] if overrides.get('ca_expected_max_v') is None else overrides['ca_expected_max_v']),
                'current_range_mode': str(self.config['current_range_mode'] if overrides.get('current_range_mode') is None else overrides['current_range_mode']),
            }
        if mode == 'sine':
            return {
                'sine_dc_offset': float(self.config['sine_dc_offset'] if overrides.get('sine_dc_offset') is None else overrides['sine_dc_offset']),
                'sine_amplitude': float(self.config['sine_amplitude'] if overrides.get('sine_amplitude') is None else overrides['sine_amplitude']),
                'sine_frequency': float(self.config['sine_frequency'] if overrides.get('sine_frequency') is None else overrides['sine_frequency']),
                'sine_acq_frequency': float(self.config['sine_acq_frequency'] if overrides.get('sine_acq_frequency') is None else overrides['sine_acq_frequency']),
                'sine_total_time': float(self.config['sine_total_time'] if overrides.get('sine_total_time') is None else overrides['sine_total_time']),
                'sine_phase_offset': float(self.config['sine_phase_offset'] if overrides.get('sine_phase_offset') is None else overrides['sine_phase_offset']),
                'current_range_mode': str(self.config['current_range_mode'] if overrides.get('current_range_mode') is None else overrides['current_range_mode']),
            }
        if mode == 'dpv':
            return {
                'dpv_initial_voltage': float(self.config['dpv_initial_voltage'] if overrides.get('dpv_initial_voltage') is None else overrides['dpv_initial_voltage']),
                'dpv_final_voltage': float(self.config['dpv_final_voltage'] if overrides.get('dpv_final_voltage') is None else overrides['dpv_final_voltage']),
                'dpv_step_size': float(self.config['dpv_step_size'] if overrides.get('dpv_step_size') is None else overrides['dpv_step_size']),
                'dpv_pulse_size': float(self.config['dpv_pulse_size'] if overrides.get('dpv_pulse_size') is None else overrides['dpv_pulse_size']),
                'dpv_sample_period': float(self.config['dpv_sample_period'] if overrides.get('dpv_sample_period') is None else overrides['dpv_sample_period']),
                'dpv_pulse_time': float(self.config['dpv_pulse_time'] if overrides.get('dpv_pulse_time') is None else overrides['dpv_pulse_time']),
                'dpv_noise_rejection': self._coerce_bool(self.config['dpv_noise_rejection'] if overrides.get('dpv_noise_rejection') is None else overrides['dpv_noise_rejection']),
                'dpv_irange_mode': str(self.config['dpv_irange_mode'] if overrides.get('dpv_irange_mode') is None else overrides['dpv_irange_mode']),
                'dpv_max_current': float(self.config['dpv_max_current'] if overrides.get('dpv_max_current') is None else overrides['dpv_max_current']),
                'current_range_mode': str(self.config['current_range_mode'] if overrides.get('current_range_mode') is None else overrides['current_range_mode']),
            }
        raise ValueError(f'Unsupported measurement mode: {mode}')

    def _remote_dataset(self, task_name: str, measurement_mode: str, instrument_name: Optional[str] = None, **kwargs) -> xr.Dataset:
        client = self._get_client()
        payload = self._measurement_parameters(measurement_mode, kwargs)
        payload['instrument_name'] = self.config['instrument_name'] if instrument_name is None else str(instrument_name)
        remote_task_uuid = client.enqueue(task_name=task_name, **payload)
        meta = client.wait(target_uuid=remote_task_uuid, first_check_delay=0.5)
        if meta.get('exit_state') == 'Error!':
            raise RuntimeError(meta.get('return_val'))
        return_val = meta.get('return_val')
        if isinstance(return_val, dict):
            dataset_payload = return_val.get('dataset')
            if isinstance(dataset_payload, dict):
                return self._dataset_from_payload(dataset_payload)
        if return_val == 'xarray.Dataset':
            dataset = client.retrieve_obj(remote_task_uuid)
            if isinstance(dataset, xr.Dataset):
                return dataset
            raise TypeError(
                f"Remote task {task_name} for uuid={remote_task_uuid} stored a non-dataset object: "
                f"type={type(dataset).__name__}, value={dataset!r}"
            )
        raise TypeError(
            f"Remote task {task_name} for uuid={remote_task_uuid} returned unsupported payload: "
            f"type={type(return_val).__name__}, value={return_val!r}"
        )

    @Driver.unqueued()
    def ping(self):
        client = self._get_client()
        return {
            'status': 'ok',
            'server_url': self._server_url(),
            'driver_status': client.driver_status(),
        }

    @Driver.unqueued()
    def startBridge(self):
        client = self._get_client()
        return client.query_driver(r='startService')

    @Driver.unqueued()
    def connectInstrument(self, instrument_name: Optional[str] = None):
        client = self._get_client()
        target = self.config['instrument_name'] if instrument_name is None else str(instrument_name)
        self.config['instrument_name'] = target
        return client.query_driver(r='connectInstrument', instrument_name=target)

    @Driver.queued()
    def runCV(self, instrument_name: Optional[str] = None, **kwargs):
        return self._remote_dataset('runCV', 'cv', instrument_name=instrument_name, **kwargs)

    @Driver.queued()
    def runCA(self, instrument_name: Optional[str] = None, **kwargs):
        return self._remote_dataset('runCA', 'ca', instrument_name=instrument_name, **kwargs)

    @Driver.queued()
    def runSine(self, instrument_name: Optional[str] = None, **kwargs):
        return self._remote_dataset('runSine', 'sine', instrument_name=instrument_name, **kwargs)

    @Driver.queued()
    def runDPV(self, instrument_name: Optional[str] = None, **kwargs):
        return self._remote_dataset('runDPV', 'dpv', instrument_name=instrument_name, **kwargs)


_DEFAULT_CUSTOM_CONFIG = {
    '_classname': 'AFL.automation.instrument.GamryHTTPDriver.GamryHTTPDriver',
}
_DEFAULT_PORT = 5052

if __name__ == '__main__':
    from AFL.automation.shared.launcher import *
