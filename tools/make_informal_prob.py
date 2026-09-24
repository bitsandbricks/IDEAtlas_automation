#!/usr/bin/env python3
"""Persist the continuous informal/DUA class probability for a city.

Reruns the exact sliding-window inference used by ``inference_mbcnn`` but,
instead of the ``argmax + 1`` class map, writes the averaged softmax of the
informal class (``averaged_predictions[..., informal_idx]``) as a float32
raster, clipped to the city AOI, next to the classification map in
``ai-dua-mapping/output``.

The framework is used read-only (its ``PatchGenerator``, Hann window and
model builder); ``tools/inference.py`` and the normal pipeline are untouched.

Usage (inside the ``ideatlas`` conda env, after ``make city`` produced the
inputs):

    make prob CITY=encarnacion
    # or:
    python tools/make_informal_prob.py --city encarnacion [--year 2025]

Output example::

    ai-dua-mapping/output/encarnacion_paraguay.s2.bd.mbcnn.2025_informal_prob.tif

The informal channel defaults to the last softmax class (``classes - 1``),
which is the highest ``LABEL_INDICES`` label under both the '012' and '123'
schemes; override it with ``--informal-idx``.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ai-dua-mapping"))


def resolve_informal_idx(informal_idx: int, n_classes: int) -> int:
    """Return the zero-based softmax channel of the informal (DUA) class.

    ``-1`` (the default) means 'last class', the invariant that holds for
    both the '012' and '123' label schemes; explicit indices pass through.
    """
    if informal_idx == -1:
        informal_idx = n_classes - 1
    if not 0 <= informal_idx < n_classes:
        raise ValueError(
            f"informal_idx {informal_idx} out of range for {n_classes} classes"
        )
    return informal_idx


def derive_output_name(norm: str, model: str, year: int) -> str:
    """Name mirroring ``classified_raster_name`` plus the _informal_prob suffix."""
    return f"{norm}.s2.bd.{model}.{year}_informal_prob.tif"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", required=True, help="City name (config/cities/<city>.yaml).")
    parser.add_argument("--year", type=int, default=None, help="Processing year (default from config).")
    parser.add_argument("--model", default="mbcnn", help="Model architecture (default: mbcnn).")
    parser.add_argument("--weights", default=None, help="Model weights .h5 (default: city, else global weights).")
    parser.add_argument("--out", default=None, help="Output folder (default: ai-dua-mapping/output).")
    parser.add_argument("--batch-size", type=int, default=8, help="Inference batch size (default: 8).")
    parser.add_argument("--stride-ratio", type=float, default=0.5, help="Patch stride as a patch-size ratio (default: 0.5).")
    parser.add_argument("--informal-idx", type=int, default=-1, help="Softmax channel of the informal class (-1 = last).")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # Heavy imports stay inside so the pure helpers can be unit-tested
    # without TensorFlow/rasterio/geopandas installed.
    import numpy as np
    import rasterio as rio
    import geopandas as gpd
    from rasterio.mask import mask
    from tqdm import tqdm

    from utils.inference import PatchGenerator, hann_window
    from utils.dataloader import normalize_s2
    from models.utils import select_model
    from utils.configs import load_configs
    from pipeline.config import load_config

    cfg = load_config(args.city, year=args.year)
    norm = cfg.city_normalized

    s2_path = os.path.join(
        cfg.ai_dua_mapping_dir, "data", "raw", "sentinel", norm, f"S2_{cfg.year}.tif"
    )
    bd_path = os.path.join(
        cfg.ai_dua_mapping_dir, "data", "raw", "buildings", "density", f"{norm}_bd.tif"
    )
    for label, path in (("S2", s2_path), ("BD density", bd_path)):
        if not os.path.exists(path):
            print(
                f"ERROR: {label} input not found: {path}\n"
                "Are the inputs prepared? Run 'make city CITY=<city>' first.",
                file=sys.stderr,
            )
            return 1

    weights = args.weights or (
        cfg.city_weights_path
        if os.path.exists(cfg.city_weights_path)
        else cfg.global_weights_path
    )
    if not weights or not os.path.exists(weights):
        print(f"ERROR: no model weights found at {weights!r}.", file=sys.stderr)
        return 1

    framework_cfg = load_configs(os.path.join(cfg.ai_dua_mapping_dir, "config.yaml"))
    model = select_model(args.model, framework_cfg)
    model.load_weights(weights)
    print(f"Loaded model '{args.model}' and weights from: {weights}")

    rasters = [rio.open(src).read().transpose(1, 2, 0) for src in (s2_path, bd_path)]
    rasters[0] = np.nan_to_num(rasters[0], nan=0.0)
    input1 = normalize_s2(rasters[0])
    input2 = rasters[1]

    patch_height, patch_width = model.inputs[0].shape[1:3]
    stride = int(patch_height * args.stride_ratio)
    image_height, image_width = input1.shape[:2]
    n_classes = model.outputs[0].shape[-1]

    y_pred = np.zeros((image_height, image_width, n_classes), dtype=np.float32)
    count_map = np.zeros((image_height, image_width, n_classes), dtype=np.float32)

    dataset = PatchGenerator(
        {"S2": input1, "BD": input2},
        patch_height,
        patch_width,
        stride,
        args.batch_size,
    )
    window = hann_window(patch_height)[..., np.newaxis]
    pbar = tqdm(total=len(dataset), desc="Progress")
    for batch in dataset:
        input_patches, batch_coords = batch
        batch_predictions = model.predict(input_patches, verbose=0)
        for i in range(len(batch_predictions)):
            y, x = batch_coords[i]
            patch_prediction = batch_predictions[i] * window
            y_pred[y : y + patch_height, x : x + patch_width] += patch_prediction
            count_map[y : y + patch_height, x : x + patch_width] += window
        pbar.update(1)
    pbar.close()

    averaged_predictions = np.divide(
        y_pred, count_map, out=np.zeros_like(y_pred), where=(count_map != 0)
    )
    informal_idx = resolve_informal_idx(args.informal_idx, n_classes)
    informal_probs = averaged_predictions[..., informal_idx]

    def save_float_raster(array, reference_image_path, save_path, aoi_path, nodata=np.nan):
        with rio.open(reference_image_path) as src:
            profile = src.profile.copy()
            transform = src.transform
        profile.update(dtype="float32", count=1, nodata=nodata, compress="lzw")
        if aoi_path:
            aoi = gpd.read_file(aoi_path)
            aoi = aoi.to_crs(profile["crs"])
            with rio.MemoryFile() as memfile:
                with memfile.open(**profile) as mem_raster:
                    mem_raster.write(array, 1)
                    clipped, clipped_transform = mask(mem_raster, aoi.geometry, crop=True)
            profile.update(
                height=clipped.shape[1],
                width=clipped.shape[2],
                transform=clipped_transform,
            )
            with rio.open(save_path, "w", **profile) as dst:
                dst.write(clipped[0], 1)
        else:
            with rio.open(save_path, "w", **profile) as dst:
                dst.write(array, 1)

    out_dir = args.out or cfg.prediction_dir
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, derive_output_name(norm, args.model, cfg.year))
    save_float_raster(informal_probs, s2_path, out_path, cfg.aoi_path)

    print(f"\nInformal probability raster (channel {informal_idx}) saved to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())