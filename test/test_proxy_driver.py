import pytest

from AFL.automation.APIServer.Driver import ProxyConnectionError, ProxyDriver


def test_proxy_connection_error_identifies_unreachable_server():
    driver = ProxyDriver(name="test-proxy")

    def unavailable_client(**kwargs):
        raise RuntimeError("connection refused")

    with pytest.raises(
        ProxyConnectionError,
        match=r"Cannot connect camera proxy to AFL APIServer at camera\.test:5007",
    ) as error:
        driver.get_proxy_client(
            "camera",
            ip="camera.test",
            port="5007",
            client_factory=unavailable_client,
        )

    assert "network reachability" in str(error.value)
