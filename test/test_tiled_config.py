import json

import pytest

from AFL.automation.shared.tiled import (
    TiledConfigurationError,
    get_tiled_client,
    resolve_tiled_config,
)


def _write_config(afl_home, config):
    afl_home.mkdir()
    (afl_home / "config.json").write_text(json.dumps(config))


def test_resolve_tiled_config_uses_newest_entry_with_server(tmp_path):
    afl_home = tmp_path / "afl"
    _write_config(
        afl_home,
        {
            "26/04/08 10:00:00.000000": {
                "tiled_server": "https://older.example",
                "tiled_api_key": "older-key",
            },
            "26/04/08 11:00:00.000000": {"owner_email": "user@example.com"},
            "26/04/08 12:00:00.000000": {
                "tiled_server": " https://newer.example ",
                "tiled_api_key": " newer-key ",
            },
        },
    )

    assert resolve_tiled_config(afl_home) == (
        "https://newer.example",
        "newer-key",
    )


def test_resolve_tiled_config_allows_anonymous_server(tmp_path):
    afl_home = tmp_path / "afl"
    _write_config(
        afl_home,
        {"26/04/08 12:00:00.000000": {"tiled_server": "https://public.example"}},
    )

    assert resolve_tiled_config(afl_home) == ("https://public.example", "")


def test_resolve_tiled_config_raises_when_server_is_missing(tmp_path):
    afl_home = tmp_path / "afl"
    _write_config(afl_home, {"26/04/08 12:00:00.000000": {"owner_email": "user"}})

    with pytest.raises(TiledConfigurationError, match="No tiled_server"):
        resolve_tiled_config(afl_home)


def test_get_tiled_client_uses_resolved_config(monkeypatch, tmp_path):
    afl_home = tmp_path / "afl"
    _write_config(
        afl_home,
        {"26/04/08 12:00:00.000000": {
            "tiled_server": "https://tiled.example",
            "tiled_api_key": "api-key",
        }},
    )
    captured = {}

    def fake_from_uri(server, **kwargs):
        captured["server"] = server
        captured.update(kwargs)
        return "tiled-client"

    monkeypatch.setattr("tiled.client.from_uri", fake_from_uri)

    assert get_tiled_client(afl_home) == "tiled-client"
    assert captured == {
        "server": "https://tiled.example",
        "api_key": "api-key",
        "structure_clients": "dask",
    }
