from pathlib import Path
from unittest import mock

from unshuffle.core.config import ensure_default_config, load_config, reset_config_cache
from unshuffle.core.logging import logger, setup_logging


def test_load_config_does_not_create_missing_file(tmp_path: Path) -> None:
    root = tmp_path
    (root / "data" / "taxonomy").mkdir(parents=True)

    with mock.patch("unshuffle.core.config.ROOT_DIR", root):
        reset_config_cache()
        config = load_config()

    assert isinstance(config["ALIAS_TABLE"], dict)
    assert not (root / "data" / "config.json").exists()


def test_ensure_default_config_creates_missing_file(tmp_path: Path) -> None:
    root = tmp_path
    (root / "data").mkdir(parents=True)

    with mock.patch("unshuffle.core.config.ROOT_DIR", root):
        reset_config_cache()
        config_path = ensure_default_config()

    assert config_path == root / "data" / "config.json"
    assert config_path.exists()


def test_load_config_preserves_list_order_when_merging(tmp_path: Path) -> None:
    root = tmp_path
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "taxonomy").mkdir()
    (data_dir / "config.json").write_text(
        """
        {
            "LOOP_INDICATORS": ["alpha", "beta", "gamma", "beta"],
            "ONESHOT_INDICATORS": ["shot", "snap", "shot"]
        }
        """.strip(),
        encoding="utf-8",
    )

    with mock.patch("unshuffle.core.config.ROOT_DIR", root):
        reset_config_cache()
        config = load_config()

    assert config["LOOP_INDICATORS"] == ["alpha", "beta", "gamma"]
    assert config["ONESHOT_INDICATORS"] == ["shot", "snap"]


def test_setup_logging_uses_configured_log_level(tmp_path: Path) -> None:
    root = tmp_path
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "taxonomy").mkdir()
    (data_dir / "config.json").write_text('{"LOG_LEVEL": "DEBUG"}', encoding="utf-8")

    with mock.patch("unshuffle.core.config.ROOT_DIR", root), mock.patch("unshuffle.core.logging.get_system_dir", return_value=tmp_path):
        reset_config_cache()
        setup_logging(Path("Library"), False, "session-1")

    assert logger.level == 10
