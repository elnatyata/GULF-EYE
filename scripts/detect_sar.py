#!/usr/bin/env python3
"""Steps 2 and 3 of the pipeline: Sentinel-1 scene -> CLAHE -> YOLOv8 -> detections in the dashboard's format.

    pip install ultralytics opencv-python rasterio numpy
    # 1. download a scene or burst (VV band), for example with smart-sentinel-downloader:
    #    sentinel-dl search --bbox 3.0 4.0 6.0 7.0 --start 2026-11-01 --end 2026-11-03 --download --bands vv
    # 2. run the detector on the GeoTIFF:
    python3 scripts/detect_sar.py --scene path/to/scene_vv.tif --weights path/to/yolov8m_ships.pt \\
        --time 2026-11-04T05:40:00Z --out web/data/sar_live.json

The weights must be a YOLOv8 medium model trained on SAR ship data (for example a public ship-detection set such as
SSDD or HRSID). The default ultralytics weights are trained on photographs and will not work on radar images.
Pixel boxes are converted to longitude and latitude with the GeoTIFF's affine transform; the ship length is the
longer box side times the pixel size, so it is only a rough estimate.

NOT TESTED in this repository (no SAR scene, GPU or trained weights were available when it was written).
"""
import argparse
import json
import math

KM_PER_DEG = 111.32


def clahe(img8):
    import cv2
    return cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(img8)


def to_uint8(band):
    import numpy as np
    lo, hi = np.nanpercentile(band, [2, 98])
    return (np.clip((band - lo) / max(hi - lo, 1e-6), 0, 1) * 255).astype("uint8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True, help="GeoTIFF, VV polarisation")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--time", required=True, help="acquisition time, ISO 8601 UTC")
    ap.add_argument("--out", default="web/data/sar_live.json")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--tile", type=int, default=1024)
    args = ap.parse_args()

    import numpy as np
    import rasterio
    from rasterio.warp import transform_bounds
    from ultralytics import YOLO

    model = YOLO(args.weights)
    dets = []
    with rasterio.open(args.scene) as src:
        band = src.read(1).astype("float32")
        img = clahe(to_uint8(band))
        res_m = abs(src.transform.a) * (KM_PER_DEG * 1000 if src.crs.is_geographic else 1)
        h, w = img.shape
        for y0 in range(0, h, args.tile):  # tiles keep memory low and small boats visible
            for x0 in range(0, w, args.tile):
                tile = img[y0:y0 + args.tile, x0:x0 + args.tile]
                rgb = np.stack([tile] * 3, axis=-1)
                for r in model.predict(rgb, conf=args.conf, imgsz=args.tile, verbose=False):
                    for box, conf in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy()):
                        cx, cy = x0 + (box[0] + box[2]) / 2, y0 + (box[1] + box[3]) / 2
                        lon, lat = src.xy(cy, cx)
                        length = max(box[2] - box[0], box[3] - box[1]) * res_m
                        dets.append({"lat": round(lat, 4), "lon": round(lon, 4), "length_m": int(length),
                                     "confidence": round(float(conf), 2), "class": "vessel"})
        left, bottom, right, top = transform_bounds(src.crs, "EPSG:4326", *src.bounds)

    dets.sort(key=lambda d: (d["lon"], d["lat"]))
    for i, d in enumerate(dets, 1):
        d["id"] = f"D-{i:03d}"
    footprint = [[left, top], [right, top], [right, bottom], [left, bottom]]
    out = {"scene": {"id": args.scene.split("/")[-1], "time": args.time, "polarisation": "VV",
                     "preprocessing": "CLAHE contrast enhancement", "detector": f"YOLOv8 ({args.weights})",
                     "footprint": footprint, "synthetic": False},
           "detections": dets, "history": {}}
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    print(f"{len(dets)} detections -> {args.out}")


if __name__ == "__main__":
    main()
