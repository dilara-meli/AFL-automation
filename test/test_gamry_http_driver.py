from AFL.automation.instrument.GamryHTTPDriver import GamryHTTPDriver


class FakeClient:
    def __init__(self, ip=None, port='5000', interactive=False):
        self.ip = ip
        self.port = port
        self.interactive = interactive
        self.logged_in_username = None
        self.calls = []

    def login(self, username, populate_commands=True):
        self.logged_in_username = username

    def logged_in(self):
        return self.logged_in_username is not None

    def driver_status(self):
        self.calls.append(('driver_status', {}, None))
        return {'status': 'ok', 'driver': 'remote-gamry'}

    def validateConnection(self, **kwargs):
        self.calls.append(('validateConnection', kwargs, None))
        return {'status': 'ok', 'validated': True}

    def connectInstrument(self, **kwargs):
        self.calls.append(('connectInstrument', kwargs, None))
        return {'status': 'ok', 'instrument_name': kwargs.get('instrument_name', 'PSTAT')}

    def startService(self, **kwargs):
        self.calls.append(('startService', kwargs, None))
        return {'status': 'ok'}

    def listInstruments(self, **kwargs):
        self.calls.append(('listInstruments', kwargs, None))
        return {'status': 'ok', 'result': ['PSTAT']}

    def runMeasurementNow(self, **kwargs):
        self.calls.append(('runMeasurementNow', kwargs, None))
        return {'status': 'ok', 'result': {'measurement_mode': kwargs.get('measurement_mode')}}

    def enqueue(self, interactive=None, **kwargs):
        self.calls.append(('enqueue', kwargs, interactive))
        return {'task_uuid': 'remote-task-1', 'task_name': kwargs['task_name']}


def test_status_reports_remote_endpoint(monkeypatch):
    monkeypatch.setattr('AFL.automation.instrument.GamryHTTPDriver.Client', FakeClient)
    driver = GamryHTTPDriver(overrides={'remote_host': 'gamry-win', 'remote_port': '5051'})

    status = driver.status()

    assert 'remote_host=gamry-win' in status
    assert 'remote_port=5051' in status


def test_ping_remote_uses_client_and_login(monkeypatch):
    monkeypatch.setattr('AFL.automation.instrument.GamryHTTPDriver.Client', FakeClient)
    driver = GamryHTTPDriver(
        overrides={
            'remote_host': 'gamry-win',
            'remote_port': '5051',
            'remote_login': True,
            'remote_username': 'afl',
        }
    )

    result = driver.pingRemote()

    assert result == {
        'status': 'ok',
        'remote_host': 'gamry-win',
        'remote_port': '5051',
        'logged_in': True,
    }


def test_unqueued_methods_forward_to_remote_client(monkeypatch):
    monkeypatch.setattr('AFL.automation.instrument.GamryHTTPDriver.Client', FakeClient)
    driver = GamryHTTPDriver(overrides={'remote_host': 'gamry-win', 'remote_port': '5051'})

    status = driver.getRemoteDriverStatus()
    validation = driver.validateConnection()
    connection = driver.connectInstrument(instrument_name='PSTAT-1')
    measurement = driver.runMeasurementNow(measurement_mode='ca', instrument_name='PSTAT-1', ca_step1_time=2.0)

    assert status['driver'] == 'remote-gamry'
    assert validation['validated'] is True
    assert connection['instrument_name'] == 'PSTAT-1'
    assert measurement['result']['measurement_mode'] == 'ca'


def test_queued_methods_forward_task_name_and_kwargs(monkeypatch):
    monkeypatch.setattr('AFL.automation.instrument.GamryHTTPDriver.Client', FakeClient)
    driver = GamryHTTPDriver(overrides={'forward_interactive': False})

    result = driver.runDepositionCA(instrument_name='PSTAT-1', ca_step1_time=2.0)

    assert result == {'task_uuid': 'remote-task-1', 'task_name': 'runDepositionCA'}
    assert driver._client.calls[-1] == (
        'enqueue',
        {'task_name': 'runDepositionCA', 'instrument_name': 'PSTAT-1', 'ca_step1_time': 2.0},
        False,
    )


def test_remote_login_requires_username(monkeypatch):
    monkeypatch.setattr('AFL.automation.instrument.GamryHTTPDriver.Client', FakeClient)
    driver = GamryHTTPDriver(overrides={'remote_login': True})

    try:
        driver.pingRemote()
    except ValueError as exc:
        assert 'remote_username is required' in str(exc)
    else:
        raise AssertionError('Expected ValueError when remote_login is enabled without remote_username')
