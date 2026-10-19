#!/usr/bin/env python3
"""Extract a handful of evenly spaced keyframes from a clip as JPEGs."""

import argparse
import os

import imageio.v2 as imageio
from PIL import Image


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out_dir", default="keyframes")
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--max_w", type=int, default=768)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    reader = imageio.get_reader(args.video)
    frames = [f for f in reader]
    reader.close()
    total = len(frames)
    idxs = [int(round(i * (total - 1) / (args.n + 1))) for i in range(1, args.n + 1)]
    paths = []
    for k, idx in enumerate(idxs):
        img = Image.fromarray(frames[idx]).convert("RGB")
        if img.width > args.max_w:
            h = int(img.height * args.max_w / img.width)
            img = img.resize((args.max_w, h))
        p = os.path.join(args.out_dir, f"kf_{k:02d}.jpg")
        img.save(p, "JPEG", quality=88)
        paths.append(p)
        print(f"frame {idx}/{total} -> {p}")
    print("WROTE", len(paths), "keyframes")


if __name__ == "__main__":
    main()
