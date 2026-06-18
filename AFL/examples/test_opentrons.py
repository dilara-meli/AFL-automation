"""Minimal OT-2 demo for AFL automation.

Start the server:

    python -m AFL.examples.test_opentrons --serve --robot-ip 169.254.59.185

Run the demo locally against the OT-2:

    python -m AFL.examples.test_opentrons --demo --robot-ip 169.254.59.185

The demo only performs a few basic actions:
- home the gantry
- load a tip rack
- load a pipette
- pick up one tip
- drop that tip back in place
"""

from __future__ import annotations

import argparse

from AFL.automation.APIServer.APIServer import APIServer
from AFL.automation.prepare.OT2HTTPDriver import OT2HTTPDriver


def start_server(host: str, port: int, robot_ip: str, robot_port: int) -> None:
    driver = OT2HTTPDriver(
        overrides={
            "robot_ip": robot_ip,
            "robot_port": str(robot_port),
        }
    )
    server = APIServer("ot2_demo")
    server.add_standard_routes()
    server.create_queue(driver)
    server.run(host=host, port=port)


def run_demo(robot_ip: str, robot_port: int) -> None:
    driver = OT2HTTPDriver(
        overrides={
            "robot_ip": robot_ip,
            "robot_port": str(robot_port),
        }
    )

    driver.home()
    driver.load_labware(name="opentrons_96_tiprack_1000ul", slot="1")
    driver.load_instrument(name="p1000_single_gen2", mount="left", tip_rack_slots=["1"])

    pipette_id = driver.pipette_info["left"]["id"]
    tiprack_id, tip_well = driver.get_tip("left")
    driver.has_tip = True
    driver.last_pipette = "left"

    driver._execute_atomic_command(
        "pickUpTip",
        {
            "pipetteId": pipette_id,
            "labwareId": tiprack_id,
            "wellName": tip_well,
            "wellLocation": {
                "origin": "top",
                "offset": {"x": 0, "y": 0, "z": 0},
            },
        },
    )
    driver._execute_atomic_command(
        "dropTipInPlace",
        {
            "pipetteId": pipette_id,
        },
    )
    driver.has_tip = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal AFL OT-2 demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5002)
    parser.add_argument("--username", default="ot2")
    parser.add_argument("--robot-ip", default="127.0.0.1")
    parser.add_argument("--robot-port", type=int, default=31950)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--demo", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.serve:
        start_server(args.host, args.port, args.robot_ip, args.robot_port)
        return

    if args.demo:
        run_demo(args.robot_ip, args.robot_port)
        return

    raise SystemExit("Use --serve to start the server or --demo to run the minimal OT-2 demo.")


if __name__ == "__main__":
    main()