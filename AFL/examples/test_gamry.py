"""Minimal Gamry demo for AFL automation.

List available instruments through the AFL driver and RPyC bridge:

    python -m AFL.examples.test_gamry --list-instruments

Validate the configured instrument connection through the AFL driver:

    python -m AFL.examples.test_gamry --validate --instrument-name PSTAT

Run an AFL driver diagnostic that is also available through the API server:

    python -m AFL.examples.test_gamry --diagnose --instrument-name PSTAT

Run a direct vendor-env diagnostic without the AFL driver:

    python -m AFL.examples.test_gamry --worker-diagnose --instrument-name PSTAT

Run a cyclic voltammetry collection directly through the driver:

    python -m AFL.examples.test_gamry --demo --instrument-name PSTAT

Start an API server exposing the Gamry driver:

    python -m AFL.examples.test_gamry --serve --host 127.0.0.1 --port 5051

Then open the browser-native Gamry panel at:

    http://127.0.0.1:5051/gamry_panel
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
from pathlib import Path

from AFL.automation.APIServer.APIServer import APIServer
from AFL.automation.APIServer.data.DataTiled import DataTiled
from AFL.automation.instrument.GamryDriver import GamryDriver


def build_driver(args: argparse.Namespace) -> GamryDriver:
    overrides = {
        "process_name": args.process_name,
        "subprocess_timeout": float(args.subprocess_timeout),
        "initial_voltage": float(args.initial_voltage),
        "apex1_voltage": float(args.apex1_voltage),
        "apex2_voltage": float(args.apex2_voltage),
        "final_voltage": float(args.final_voltage),
        "apex1_hold": float(args.apex1_hold),
        "apex2_hold": float(args.apex2_hold),
        "final_hold": float(args.final_hold),
        "scan_rate": float(args.scan_rate),
        "step_size": float(args.step_size),
        "cycles": int(args.cycles),
        "scan_delay": float(args.scan_delay),
        "current_range_mode": args.current_range_mode,
    }
    if args.worker_path:
        overrides["worker_path"] = str(Path(args.worker_path))

    return GamryDriver(
        gamry_env_path=args.gamry_env_path,
        instrument_name=args.instrument_name,
        overrides=overrides,
    )


def _read_global_tiled_config() -> dict:
    config_path = Path.home() / '.afl' / 'config.json'
    if not config_path.exists():
        return {}
    try:
        config_data = json.loads(config_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(config_data, dict) or not config_data:
        return {}

    datetime_key_format = '%y/%d/%m %H:%M:%S.%f'
    try:
        keys = sorted(
            config_data.keys(),
            key=lambda key: datetime.datetime.strptime(key, datetime_key_format),
            reverse=True,
        )
    except ValueError:
        keys = sorted(config_data.keys(), reverse=True)

    for key in keys:
        entry = config_data.get(key)
        if not isinstance(entry, dict):
            continue
        server = str(entry.get('tiled_server', '')).strip()
        api_key = str(entry.get('tiled_api_key', '')).strip()
        if server or api_key:
            return {
                'tiled_server': server,
                'tiled_api_key': api_key,
            }
    return {}


def build_data_backend(args: argparse.Namespace):
    config = _read_global_tiled_config()
    tiled_uri = str(args.tiled_uri or config.get('tiled_server', '')).strip()
    tiled_api_key = str(args.tiled_api_key or config.get('tiled_api_key', '')).strip()
    tiled_backup_path = str(args.tiled_backup_path).strip()

    if not tiled_uri:
        return None
    if not tiled_api_key:
        raise ValueError('No Tiled API key found. Set --tiled-api-key, TILED_API_KEY, or ~/.afl/config.json.')
    if not tiled_backup_path:
        raise ValueError('--tiled-backup-path is required when Tiled is enabled')
    return DataTiled(tiled_uri, tiled_api_key, tiled_backup_path)


def start_server(args: argparse.Namespace) -> None:
    driver = build_driver(args)
    data = build_data_backend(args)
    server = APIServer("gamry_demo", data=data)
    server.add_standard_routes()
    server.create_queue(driver)
    server.run(host=args.host, port=args.port)


def list_instruments(args: argparse.Namespace) -> None:
    driver = build_driver(args)
    result = driver.listInstruments()
    print(json.dumps(result, indent=2))


def validate_connection(args: argparse.Namespace) -> None:
    driver = build_driver(args)
    result = driver.validateConnection()
    print(json.dumps(result, indent=2))


def run_diagnostic(args: argparse.Namespace) -> None:
    driver = build_driver(args)
    result = driver.diagnoseConnection(args.instrument_name)
    print(json.dumps(result, indent=2))


def run_worker_diagnostic(args: argparse.Namespace) -> None:
    worker_path = Path(args.worker_path) if args.worker_path else Path(__file__).resolve().parents[1] / 'automation' / 'instrument' / 'gamry_worker.py'
    env_path = Path(args.gamry_env_path)
    python_path = env_path / 'Scripts' / 'python.exe'
    if not python_path.exists():
        raise FileNotFoundError(f"Gamry virtual environment interpreter not found: {python_path}")
    if not worker_path.exists():
        raise FileNotFoundError(f"Gamry worker script not found: {worker_path}")

    command = [str(python_path), str(worker_path), 'diagnose']
    if args.instrument_name:
        command.append(args.instrument_name)
    command.append(args.process_name)

    launch_env = os.environ.copy()
    launch_env.pop('PYTHONHOME', None)
    launch_env.pop('PYTHONPATH', None)

    completed = subprocess.run(
        command,
        cwd=str(env_path),
        env=launch_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout.strip())
    if completed.returncode != 0:
        stderr = completed.stderr.strip() if completed.stderr else '<empty>'
        raise RuntimeError(f"Gamry diagnostic failed with code {completed.returncode}: {stderr}")


def run_demo(args: argparse.Namespace) -> None:
    driver = build_driver(args)
    dataset = driver.collectCV(
        initial_voltage=args.initial_voltage,
        apex1_voltage=args.apex1_voltage,
        apex2_voltage=args.apex2_voltage,
        final_voltage=args.final_voltage,
        apex1_hold=args.apex1_hold,
        apex2_hold=args.apex2_hold,
        final_hold=args.final_hold,
        scan_rate=args.scan_rate,
        step_size=args.step_size,
        cycles=args.cycles,
        scan_delay=args.scan_delay,
        current_range_mode=args.current_range_mode,
    )

    print(dataset)
    print()
    print("Dataset attributes:")
    print(json.dumps(dataset.attrs, indent=2, default=str))

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        dataset.to_netcdf(output_path)
        print(f"Saved dataset to {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal AFL Gamry demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5051)
    parser.add_argument(
        "--gamry-env-path",
        default=r"C:\Users\dnm33\Documents\GamryPython\.venv",
    )
    parser.add_argument("--worker-path", default="")
    parser.add_argument("--instrument-name", default="PSTAT")
    parser.add_argument("--process-name", default="AFL_GamryDriver")
    parser.add_argument("--subprocess-timeout", type=float, default=300.0)

    parser.add_argument("--initial-voltage", type=float, default=0)
    parser.add_argument("--apex1-voltage", type=float, default=0.2)
    parser.add_argument("--apex2-voltage", type=float, default=-0.5)
    parser.add_argument("--final-voltage", type=float, default=0)
    parser.add_argument("--apex1-hold", type=float, default=0.0)
    parser.add_argument("--apex2-hold", type=float, default=0.0)
    parser.add_argument("--final-hold", type=float, default=0.0)
    parser.add_argument("--scan-rate", type=float, default=0.1)
    parser.add_argument("--step-size", type=float, default=0.01)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--scan-delay", type=float, default=0.0)
    parser.add_argument("--current-range-mode", default="auto")
    parser.add_argument("--output", default="")

    parser.add_argument('--tiled-uri', default='')
    parser.add_argument('--tiled-api-key', default=os.environ.get('TILED_API_KEY', ''))
    parser.add_argument('--tiled-backup-path', default='')

    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--list-instruments", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--worker-diagnose", action="store_true")
    parser.add_argument("--demo", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.serve:
        start_server(args)
        return

    if args.list_instruments:
        list_instruments(args)
        return

    if args.validate:
        validate_connection(args)
        return

    if args.diagnose:
        run_diagnostic(args)
        return

    if args.worker_diagnose:
        run_worker_diagnostic(args)
        return

    if args.demo:
        run_demo(args)
        return

    raise SystemExit(
        "Use --serve to start the server, --list-instruments to enumerate devices, "
        "--validate to test the configured instrument, --diagnose to run the AFL driver diagnostic, "
        "--worker-diagnose to run the vendor-env diagnostic, or --demo to collect CV data."
    )


if __name__ == "__main__":
    main()
