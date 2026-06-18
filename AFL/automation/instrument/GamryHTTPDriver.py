from __future__ import annotations

from typing import Optional

from AFL.automation.APIServer.Client import Client
from AFL.automation.APIServer.Driver import Driver


class GamryHTTPDriver(Driver):
    defaults = {}
    defaults['remote_host'] = '127.0.0.1'
    defaults['remote_port'] = '5000'
    defaults['remote_username'] = None
    defaults['remote_login'] = False
    defaults['connect_on_init'] = False
    defaults['forward_interactive'] = True

    def __init__(self, overrides=None):
        self.app = None
        self._client = None
        Driver.__init__(self, name='GamryHTTPDriver', defaults=self.gather_defaults(), overrides=overrides)
        if self.config.get('connect_on_init', False):
            self._get_client(refresh=True)

    def status(self):
        return [
            f"remote_host={self.config['remote_host']}",
            f"remote_port={self.config['remote_port']}",
            f"remote_login={bool(self.config.get('remote_login', False))}",
        ]

    def _get_client(self, refresh: bool = False) -> Client:
        if self._client is not None and not refresh:
            return self._client

        client = Client(
            ip=str(self.config['remote_host']),
            port=str(self.config['remote_port']),
            interactive=bool(self.config.get('forward_interactive', True)),
        )
        if self.config.get('remote_login', False):
            username = self.config.get('remote_username')
            if not username:
                raise ValueError('remote_username is required when remote_login is enabled')
            client.login(username)
        self._client = client
        return client

    def _remote_unqueued(self, task_name: str, **kwargs):
        client = self._get_client()
        method = getattr(client, task_name, None)
        if callable(method):
            return method(**kwargs)
        return client.server_cmd(task_name, **kwargs)

    def _remote_queued(self, task_name: str, **kwargs):
        client = self._get_client()
        return client.enqueue(
            task_name=task_name,
            interactive=bool(self.config.get('forward_interactive', True)),
            **kwargs,
        )

    @Driver.unqueued()
    def pingRemote(self):
        client = self._get_client()
        return {
            'status': 'ok',
            'remote_host': self.config['remote_host'],
            'remote_port': self.config['remote_port'],
            'logged_in': client.logged_in() if self.config.get('remote_login', False) else None,
        }

    @Driver.unqueued()
    def getRemoteDriverStatus(self):
        return self._remote_unqueued('driver_status')

    @Driver.unqueued()
    def validateConnection(self):
        return self._remote_unqueued('validateConnection')

    @Driver.unqueued()
    def connectInstrument(self, instrument_name: Optional[str] = None):
        kwargs = {}
        if instrument_name is not None:
            kwargs['instrument_name'] = instrument_name
        return self._remote_unqueued('connectInstrument', **kwargs)

    @Driver.unqueued()
    def startService(self):
        return self._remote_unqueued('startService')

    @Driver.unqueued()
    def listInstruments(self):
        return self._remote_unqueued('listInstruments')

    @Driver.unqueued()
    def runMeasurementNow(self, measurement_mode: Optional[str] = None, instrument_name: Optional[str] = None, **kwargs):
        if measurement_mode is not None:
            kwargs['measurement_mode'] = measurement_mode
        if instrument_name is not None:
            kwargs['instrument_name'] = instrument_name
        return self._remote_unqueued('runMeasurementNow', **kwargs)

    @Driver.queued()
    def runDepositionCA(self, instrument_name: Optional[str] = None, **kwargs):
        if instrument_name is not None:
            kwargs['instrument_name'] = instrument_name
        return self._remote_queued('runDepositionCA', **kwargs)

    @Driver.queued()
    def runAnalyteCA(self, instrument_name: Optional[str] = None, **kwargs):
        if instrument_name is not None:
            kwargs['instrument_name'] = instrument_name
        return self._remote_queued('runAnalyteCA', **kwargs)

    @Driver.queued()
    def runStrippingDPV(self, instrument_name: Optional[str] = None, **kwargs):
        if instrument_name is not None:
            kwargs['instrument_name'] = instrument_name
        return self._remote_queued('runStrippingDPV', **kwargs)
