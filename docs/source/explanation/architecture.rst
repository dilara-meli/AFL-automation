=========================
AFL-automation Architecture
=========================

This page explains the core architecture of AFL-automation and how its components work together.

Core Concepts
------------

AFL-automation is built around several key concepts:

1. **Drivers**: Python classes that interface with hardware or provide services
2. **APIServer**: A Flask-based web server that exposes drivers via HTTP
3. **Task Queue**: A system for managing asynchronous tasks
4. **Client**: A Python client for interacting with remote services

Architecture Diagram
------------------

The following diagram illustrates the high-level architecture:

::

    +----------------+      +-----------------+      +----------------+
    |                |      |                 |      |                |
    |  Driver Class  +----->+  API Server     +<---->+  Client        |
    |  (Hardware)    |      |  (Flask)        |      |  (Python/HTTP) |
    |                |      |                 |      |                |
    +----------------+      +-----------------+      +----------------+
                                    ^
                                    |
                                    v
                            +-----------------+
                            |                 |
                            |  Task Queue     |
                            |  (Background)   |
                            |                 |
                            +-----------------+

Driver System
-----------

The driver system is the core of AFL-automation. Drivers:

- Encapsulate hardware control logic
- Define configuration parameters with defaults
- Provide methods for interacting with hardware
- Support lazy loading of hardware-specific dependencies
- Can serve static files for custom web interfaces via `static_dirs`

API Server
---------

The API server:

- Exposes driver methods via HTTP endpoints
- Provides authentication and authorization
- Manages a task queue for asynchronous operations
- Offers a web UI for monitoring and control
- Serves static files defined by drivers for custom web interfaces

Dependency Management
-------------------

AFL-automation uses a modular dependency system:

- Core dependencies are always installed
- Hardware-specific dependencies are optional
- Lazy loading ensures code works even without all dependencies
- The extras system makes installation straightforward

For details on managing dependencies, see :doc:`/how-to/dependencies`.

Orchestrator Example: Multi-Step Gamry Sequence
-----------------------------------------------

The orchestrator treats each measurement step as an instrument entry. When multiple steps should run on the same physical Gamry potentiostat, define multiple instrument records that share one client but use different queued task names.

.. code-block:: python

    overrides = {
        'client': {
            'load': 'http://127.0.0.1:5000',
            'prep': 'http://127.0.0.1:5001',
            'gamry': 'http://127.0.0.1:5051',
        },
        'instrument': [
            {
                'name': 'gamry_deposition',
                'client_name': 'gamry',
                'measure_base_kw': {
                    'task_name': 'runDepositionCA',
                    'ca_initial_voltage': 0.0,
                    'ca_step1_voltage': -1.1,
                    'ca_step2_voltage': -1.1,
                    'ca_initial_time': 0.0,
                    'ca_step1_time': 120.0,
                    'ca_step2_time': 0.0,
                    'ca_sample_time': 0.05,
                    'current_range_mode': 'auto',
                },
                'empty_base_kw': {
                    'task_name': 'runDepositionCA',
                    'ca_initial_voltage': 0.0,
                    'ca_step1_voltage': -1.1,
                    'ca_step2_voltage': -1.1,
                    'ca_initial_time': 0.0,
                    'ca_step1_time': 120.0,
                    'ca_step2_time': 0.0,
                    'ca_sample_time': 0.05,
                    'current_range_mode': 'auto',
                },
                'concat_dim': 'time',
            },
            {
                'name': 'gamry_analyte',
                'client_name': 'gamry',
                'measure_base_kw': {
                    'task_name': 'runAnalyteCA',
                    'ca_initial_voltage': 0.0,
                    'ca_step1_voltage': -0.4,
                    'ca_step2_voltage': -0.4,
                    'ca_initial_time': 0.0,
                    'ca_step1_time': 30.0,
                    'ca_step2_time': 0.0,
                    'ca_sample_time': 0.05,
                    'current_range_mode': 'auto',
                },
                'empty_base_kw': {
                    'task_name': 'runAnalyteCA',
                    'ca_initial_voltage': 0.0,
                    'ca_step1_voltage': -0.4,
                    'ca_step2_voltage': -0.4,
                    'ca_initial_time': 0.0,
                    'ca_step1_time': 30.0,
                    'ca_step2_time': 0.0,
                    'ca_sample_time': 0.05,
                    'current_range_mode': 'auto',
                },
                'concat_dim': 'time',
            },
            {
                'name': 'gamry_stripping',
                'client_name': 'gamry',
                'measure_base_kw': {
                    'task_name': 'runStrippingDPV',
                    'dpv_initial_voltage': -1.0,
                    'dpv_final_voltage': 0.2,
                    'dpv_step_size': 0.005,
                    'dpv_pulse_size': 0.025,
                    'dpv_sample_period': 0.5,
                    'dpv_pulse_time': 0.05,
                    'dpv_noise_rejection': True,
                    'dpv_irange_mode': 'auto',
                    'dpv_max_current': 0.01,
                    'current_range_mode': 'auto',
                },
                'empty_base_kw': {
                    'task_name': 'runStrippingDPV',
                    'dpv_initial_voltage': -1.0,
                    'dpv_final_voltage': 0.2,
                    'dpv_step_size': 0.005,
                    'dpv_pulse_size': 0.025,
                    'dpv_sample_period': 0.5,
                    'dpv_pulse_time': 0.05,
                    'dpv_noise_rejection': True,
                    'dpv_irange_mode': 'auto',
                    'dpv_max_current': 0.01,
                    'current_range_mode': 'auto',
                },
                'concat_dim': 'time',
            },
        ],
    }

In this pattern, the orchestrator still uses its normal `measure()` loop. Each Gamry step is just another instrument entry, but all three entries point to the same `gamry` client. The important distinction is the queued `task_name`, which selects the correct Gamry driver method.

Each queued Gamry task returns an `xarray.Dataset` with `sample_uuid`, `sample_name`, and `task_name` stored in dataset attributes. The existing queue and data stack then writes that dataset to Tiled, allowing the orchestrator to retrieve the correct result for a given sample and step by querying on `sample_uuid` and `task_name`.
