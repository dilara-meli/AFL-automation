#!/usr/bin/env python3
"""Minimal OT2 client example for notebook-style use.

This uses the formulation values from:
`/home/nistoroboto/knv2/dev/bioformulations/bioformulation_sample_prep.pdf`

PDF example:
- BSA stock: 200 mg/mL
- YCl3 stock: 1 M
- final sample volume: 1.0 mL
- target: 175 mg/mL BSA and 9 mM YCl3

Expected transfers:
- water: 116 uL
- BSA stock: 875 uL
- YCl3 stock: 9 uL
"""

import json

from AFL.automation.APIServer.Client import Client


# Connection / execution options
SERVER_IP = "127.0.0.1"
SERVER_PORT = "5000"
USERNAME = "BioformulationUser"
DESTINATION = "4A1"
DO_DECK_SETUP = True
DO_EXECUTE_PREPARE = False

# PDF-derived stock definitions
BSA_STOCK_CONC_MG_ML = 200.0
YCL3_STOCK_CONC_M = 1.0
BSA_STOCK_LOCATION = "2A1"
YCL3_STOCK_LOCATION = "2A2"
WATER_STOCK_LOCATION = "2A3"

# Target formulation
TOTAL_VOLUME_ML = 1.0
TARGET_BSA_MG_ML = 175.0
TARGET_YCL3_MM = 9.0


client = Client(ip=SERVER_IP, port=SERVER_PORT)
client.login(USERNAME)


# Expected transfer volumes from the PDF concentration equations
total_volume_ul = TOTAL_VOLUME_ML * 1000.0
bsa_transfer_ul = (TARGET_BSA_MG_ML / BSA_STOCK_CONC_MG_ML) * total_volume_ul
ycl3_transfer_ul = ((TARGET_YCL3_MM / 1000.0) / YCL3_STOCK_CONC_M) * total_volume_ul
water_transfer_ul = total_volume_ul - bsa_transfer_ul - ycl3_transfer_ul

print("Expected pipetting actions:")
print(f"  1. Water     {WATER_STOCK_LOCATION} -> {DESTINATION}: {water_transfer_ul:.3f} uL")
print(f"  2. BSA stock {BSA_STOCK_LOCATION} -> {DESTINATION}: {bsa_transfer_ul:.3f} uL")
print(f"  3. YCl3      {YCL3_STOCK_LOCATION} -> {DESTINATION}: {ycl3_transfer_ul:.3f} uL")


if DO_DECK_SETUP:
    meta = client.enqueue(task_name="reset_deck", interactive=True)
    if meta["exit_state"] != "Success!":
        raise RuntimeError(meta["return_val"])

    meta = client.enqueue(task_name="reset", interactive=True)
    if meta["exit_state"] != "Success!":
        raise RuntimeError(meta["return_val"])

    meta = client.enqueue(
        task_name="set_config",
        interactive=True,
        minimum_volume="1 ul",
        stock_mix_order=["stock_H2O", "stock_BSA", "stock_YCl3"],
    )
    if meta["exit_state"] != "Success!":
        raise RuntimeError(meta["return_val"])

    meta = client.enqueue(
        task_name="load_labware",
        interactive=True,
        name="opentrons_96_tiprack_20ul",
        slot="10",
    )
    if meta["exit_state"] != "Success!":
        raise RuntimeError(meta["return_val"])

    meta = client.enqueue(
        task_name="load_instrument",
        interactive=True,
        name="p20_single_gen2",
        mount="left",
        tip_rack_slots=["10"],
    )
    if meta["exit_state"] != "Success!":
        raise RuntimeError(meta["return_val"])

    meta = client.enqueue(
        task_name="load_labware",
        interactive=True,
        name="opentrons_96_tiprack_300ul",
        slot="11",
    )
    if meta["exit_state"] != "Success!":
        raise RuntimeError(meta["return_val"])

    meta = client.enqueue(
        task_name="load_instrument",
        interactive=True,
        name="p300_single_gen2",
        mount="right",
        tip_rack_slots=["11"],
    )
    if meta["exit_state"] != "Success!":
        raise RuntimeError(meta["return_val"])

    meta = client.enqueue(
        task_name="load_labware",
        interactive=True,
        name="opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical",
        slot="2",
    )
    if meta["exit_state"] != "Success!":
        raise RuntimeError(meta["return_val"])

    meta = client.enqueue(
        task_name="load_labware",
        interactive=True,
        name="nest_96_wellplate_2ml_deep",
        slot="4",
    )
    if meta["exit_state"] != "Success!":
        raise RuntimeError(meta["return_val"])


# Add components to the mix database if needed
existing_components = client.query_driver(r="list_components")
try:
    existing_components = json.loads(existing_components)
except Exception:
    pass

component_names = set()
if isinstance(existing_components, list):
    for item in existing_components:
        if isinstance(item, str):
            component_names.add(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            component_names.add(item["name"])
elif isinstance(existing_components, dict):
    for key, value in existing_components.items():
        if isinstance(key, str):
            component_names.add(key)
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            component_names.add(value["name"])

if "H2O" not in component_names:
    client.query_driver(r="add_component", name="H2O", formula="H2O", density="1.0 g/ml")
if "YCl3" not in component_names:
    client.query_driver(r="add_component", name="YCl3", formula="YCl3")
if "BSA" not in component_names:
    client.query_driver(r="add_component", name="BSA")


meta = client.enqueue(task_name="reset_stocks", interactive=True)
if meta["exit_state"] != "Success!":
    raise RuntimeError(meta["return_val"])

meta = client.enqueue(
    task_name="add_stock",
    interactive=True,
    solution={
        "name": "stock_BSA",
        "location": BSA_STOCK_LOCATION,
        "concentrations": {"BSA": f"{BSA_STOCK_CONC_MG_ML} mg/ml"},
        "volumes": {"H2O": "20 ml"},
        "total_volume": "20 ml",
        "solutes": ["BSA"],
    },
)
if meta["exit_state"] != "Success!":
    raise RuntimeError(meta["return_val"])

meta = client.enqueue(
    task_name="add_stock",
    interactive=True,
    solution={
        "name": "stock_YCl3",
        "location": YCL3_STOCK_LOCATION,
        "molarities": {"YCl3": f"{YCL3_STOCK_CONC_M} mol/L"},
        "volumes": {"H2O": "10 ml"},
        "total_volume": "10 ml",
        "solutes": ["YCl3"],
    },
)
if meta["exit_state"] != "Success!":
    raise RuntimeError(meta["return_val"])

meta = client.enqueue(
    task_name="add_stock",
    interactive=True,
    solution={
        "name": "stock_H2O",
        "location": WATER_STOCK_LOCATION,
        "volumes": {"H2O": "40 ml"},
        "total_volume": "40 ml",
    },
)
if meta["exit_state"] != "Success!":
    raise RuntimeError(meta["return_val"])


target = {
    "name": f"bsa_ycl3_{TARGET_BSA_MG_ML:g}mgml_{TARGET_YCL3_MM:g}mM",
    "location": DESTINATION,
    "concentrations": {"BSA": f"{TARGET_BSA_MG_ML} mg/ml"},
    "molarities": {"YCl3": f"{TARGET_YCL3_MM} mmol/L"},
    "volumes": {"H2O": f"{TOTAL_VOLUME_ML} ml"},
    "total_volume": f"{TOTAL_VOLUME_ML} ml",
    "solutes": ["BSA", "YCl3"],
}


meta = client.enqueue(task_name="is_feasible", interactive=True, targets=target)
if meta["exit_state"] != "Success!":
    raise RuntimeError(meta["return_val"])
feasible = meta["return_val"]

print("\nServer `is_feasible(...)` result:")
print(json.dumps(feasible, indent=2))


if DO_EXECUTE_PREPARE:
    meta = client.enqueue(
        task_name="prepare",
        interactive=True,
        target=target,
        dest=DESTINATION,
    )
    if meta["exit_state"] != "Success!":
        raise RuntimeError(meta["return_val"])

    prepare_result, prepare_destination = meta["return_val"]

    print(f"\nPreparation executed at destination {prepare_destination}")
    print("\nReturned `executed_transfers`:")
    print(json.dumps(prepare_result.get("executed_transfers", []), indent=2))
else:
    print("\nPreview only. Set DO_EXECUTE_PREPARE = True to run `prepare(...)`.")
