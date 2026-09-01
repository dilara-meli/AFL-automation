import json

from AFL.automation.shared.PersistentConfig import PersistentConfig


def test_creates_missing_parent_directory_for_new_config(tmp_path):
    config_path = tmp_path / "missing" / ".afl" / "config.json"

    config = PersistentConfig(config_path, defaults={"port": 5096})

    assert config["port"] == 5096
    assert config_path.is_file()


def test_writes_plain_current_config_to_requested_path(tmp_path):
    config_path = tmp_path / ".afl" / "config.json"
    latest_path = tmp_path / ".afl" / "configs" / "config.json"

    config = PersistentConfig(config_path, defaults={"port": 5096})
    config["port"] = 5097
    config.write_current_config(latest_path)

    assert json.loads(latest_path.read_text()) == {"port": 5097}
