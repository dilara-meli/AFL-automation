import json
import os
import subprocess
import sys
from pathlib import Path

from AFL.automation.shared.PersistentConfig import PersistentConfig


def test_launcher_publishes_and_refreshes_plain_driver_config(tmp_path):
    afl_home = tmp_path / ".afl"
    launcher_script = tmp_path / "LauncherConfigDriver.py"
    launcher_script.write_text(
        """
from AFL.automation.APIServer.APIServer import APIServer
from AFL.automation.APIServer.Driver import Driver


class LauncherConfigDriver(Driver):
    def __init__(self):
        super().__init__("LauncherConfigDriver", {"port": 5000, "enabled": True})


APIServer.run = lambda self, **kwargs: None
APIServer.init_logging = lambda self, **kwargs: None
from AFL.automation.shared import launcher
""".strip()
        + "\n"
    )
    environment = os.environ | {
        "AFL_HOME": str(afl_home),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(Path(__file__).parents[1]),
    }

    subprocess.run(
        [sys.executable, str(launcher_script)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    published_path = afl_home / "configs" / "LauncherConfigDriver.config.json"
    assert json.loads(published_path.read_text()) == {"port": 5000, "enabled": True}

    driver_config = PersistentConfig(afl_home / "LauncherConfigDriver.config.json")
    driver_config["port"] = 5096
    driver_config.flush()

    subprocess.run(
        [sys.executable, str(launcher_script)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert json.loads(published_path.read_text()) == {"port": 5096, "enabled": True}
