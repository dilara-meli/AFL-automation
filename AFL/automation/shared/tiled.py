"""Shared helpers for resolving and connecting to an AFL Tiled service."""

import datetime
import json
import os
from pathlib import Path


class TiledConfigurationError(RuntimeError):
    """Raised when an AFL global configuration lacks a usable Tiled server."""


def get_afl_home(afl_home=None) -> Path:
    """Return the AFL configuration directory, honoring ``AFL_HOME``."""
    if afl_home is not None:
        return Path(afl_home).expanduser()
    configured_home = os.environ.get("AFL_HOME", "").strip()
    if configured_home:
        return Path(configured_home).expanduser()
    return Path.home() / ".afl"


def resolve_tiled_config(afl_home=None) -> tuple[str, str]:
    """Resolve the newest Tiled server and API key from AFL global config.

    AFL's :class:`PersistentConfig` stores a history keyed by timestamps in
    ``YY/DD/MM HH:MM:SS.ffffff`` format.  The newest entry that defines a
    non-empty ``tiled_server`` is selected.  An empty API key is valid for
    anonymously accessible Tiled deployments.
    """
    config_path = get_afl_home(afl_home) / "config.json"
    if not config_path.exists():
        raise TiledConfigurationError(
            f"AFL global config was not found at {config_path}"
        )

    try:
        with config_path.open() as config_file:
            config_data = json.load(config_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise TiledConfigurationError(
            f"Unable to read AFL global config at {config_path}: {exc}"
        ) from exc

    if not isinstance(config_data, dict) or not config_data:
        raise TiledConfigurationError(
            f"AFL global config at {config_path} contains no configuration entries"
        )

    timestamp_format = "%y/%d/%m %H:%M:%S.%f"
    try:
        keys = sorted(
            config_data,
            key=lambda key: datetime.datetime.strptime(key, timestamp_format),
            reverse=True,
        )
    except (TypeError, ValueError):
        keys = sorted(config_data, reverse=True)

    for key in keys:
        entry = config_data.get(key)
        if not isinstance(entry, dict):
            continue
        server = str(entry.get("tiled_server") or "").strip()
        api_key = str(entry.get("tiled_api_key") or "").strip()
        if server:
            return server, api_key

    raise TiledConfigurationError(
        f"No tiled_server found in AFL global config at {config_path}"
    )


def get_tiled_client(afl_home=None, *, structure_clients="dask"):
    """Create a Tiled client using the configured AFL Tiled credentials."""
    try:
        from tiled.client import from_uri
    except ImportError as exc:
        raise RuntimeError(
            "Tiled is required to create a Tiled client; install the tiled client extra."
        ) from exc

    tiled_server, tiled_api_key = resolve_tiled_config(afl_home)
    return from_uri(
        tiled_server,
        api_key=tiled_api_key,
        structure_clients=structure_clients,
    )
