"""Unit tests for pipeline.config."""
import pytest

from pipeline.config import load_config, load_global


def test_load_config_defaults_asuncion():
    cfg = load_config("asuncion")
    assert cfg.city == "asuncion"
    assert cfg.country == "paraguay"
    assert cfg.year == 2025
    assert cfg.model == "mbcnn"
    assert cfg.task == "classify"
    assert cfg.city_normalized == "asuncion_paraguay"
    assert cfg.retry_max_attempts >= 1
    assert cfg.retry_backoff_seconds > 0


def test_derived_framework_paths():
    cfg = load_config("encarnacion")
    assert "data/raw/aoi/encarnacion_paraguay_aoi.geojson" in cfg.aoi_path
    assert "data/raw/reference_data" in cfg.reference_dir
    assert cfg.classified_raster_name == "encarnacion_paraguay.s2.bd.mbcnn.2025.tif"
    assert "output/encarnacion_paraguay.s2.bd.mbcnn.2025.tif" in cfg.classified_raster_path
    assert cfg.city_weights_path.endswith("checkpoint/encarnacion_paraguay.s2.bd.mbcnn.weights.h5")
    assert cfg.global_weights_path.endswith("checkpoint/global.s2.bd.mbcnn.weights.h5")
    assert cfg.ghsl_pop_pattern.endswith("ghsl/pop/ENC_GHS_POP_*.tif")
    assert cfg.ghsl_built_pattern.endswith("ghsl/built/ENC_*.tif")
    assert cfg.model == "mbcnn"


def test_cli_overrides_win():
    cfg = load_config("ciudad-del-este", country="paraguay", year=2024, task="finetune")
    assert cfg.year == 2024
    assert cfg.task == "finetune"
    assert cfg.city_normalized == "ciudad_del_este_paraguay"


def test_invalid_city_raises():
    with pytest.raises(FileNotFoundError):
        load_config("atlantis")


def test_empty_city_raises():
    with pytest.raises(ValueError):
        load_config("")


def test_invalid_task_raises():
    with pytest.raises(ValueError):
        load_config("asuncion", task="solve_world_hunger")


def test_load_global():
    cfg = load_global()
    assert cfg.ai_dua_mapping_dir
    assert cfg.outputs_dir
    assert cfg.model == "mbcnn"