"""Unit tests for tools.make_informal_prob (pure helpers; no TF/rasterio)."""
import os

import pytest

from pipeline.config import load_config
from tools.make_informal_prob import (
    build_parser,
    derive_output_name,
    resolve_informal_idx,
)


def test_parser_defaults():
    args = build_parser().parse_args(["--city", "encarnacion"])
    assert args.city == "encarnacion"
    assert args.year is None
    assert args.model == "mbcnn"
    assert args.weights is None
    assert args.out is None
    assert args.batch_size == 8
    assert args.stride_ratio == 0.5
    assert args.informal_idx == -1


def test_parser_requirements():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


@pytest.mark.parametrize("n_classes", [1, 2, 3, 5])
def test_resolve_informal_idx_default_last_channel(n_classes):
    assert resolve_informal_idx(-1, n_classes) == n_classes - 1


def test_resolve_informal_idx_explicit():
    assert resolve_informal_idx(2, 3) == 2
    assert resolve_informal_idx(0, 1) == 0


def test_resolve_informal_idx_out_of_range():
    with pytest.raises(ValueError):
        resolve_informal_idx(3, 3)
    with pytest.raises(ValueError):
        resolve_informal_idx(-2, 3)


@pytest.mark.parametrize("city", ["asuncion", "encarnacion", "ciudad-del-este"])
def test_output_name_mirrors_classified_raster(city):
    """The prob raster name is the class map name + _informal_prob suffix."""
    cfg = load_config(city)
    name = derive_output_name(cfg.city_normalized, cfg.model, cfg.year)
    assert name == os.path.basename(cfg.classified_raster_path).removesuffix(".tif") + "_informal_prob.tif"


def test_output_name_format():
    assert derive_output_name("encarnacion_paraguay", "mbcnn", 2025) == (
        "encarnacion_paraguay.s2.bd.mbcnn.2025_informal_prob.tif"
    )