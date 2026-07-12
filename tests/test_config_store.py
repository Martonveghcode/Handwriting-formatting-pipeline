from pathlib import Path

from mtyh.logic.config_store import ConfigStore, default_config


def test_config_store_loads_defaults_for_missing_file(tmp_path: Path) -> None:
    config = ConfigStore(tmp_path / "missing.json").load()
    assert config["formatter"]["lines_per_page"] == 33
    assert config["image"]["default_tab"] == "stitch"
    assert config["image"]["import_dir"]
    assert config["image"]["confirm_delete_originals"] is True
    assert config["hotkeys"]["quick_copy_enabled"] is False
    assert config["appearance"]["theme"] == "system"


def test_config_store_saves_and_merges_values(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    config = default_config()
    config["macro"]["counter"] = 42
    store.save(config)

    loaded = store.load()
    assert loaded["macro"]["counter"] == 42
    assert "formatter" in loaded


def test_config_store_handles_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    loaded = ConfigStore(path).load()
    assert loaded["macro"]["prefix"] == "newtrainingdata"
