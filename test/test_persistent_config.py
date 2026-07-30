from AFL.automation.shared.PersistentConfig import PersistentConfig


def test_creates_missing_parent_directory_for_new_config(tmp_path):
    config_path = tmp_path / "missing" / ".afl" / "config.json"

    config = PersistentConfig(config_path, defaults={"port": 5096})

    assert config["port"] == 5096
    assert config_path.is_file()
