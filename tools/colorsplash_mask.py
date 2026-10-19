#!/usr/bin/env python3
"""Mask-based color splash: keep a named subject in color, rest black & white.

Uses the same mask tech as Aurora's agent (GroundingDINO + SAM) to segment a
subject by text phrase per frame, then composites: grayscale background with the
masked subject's original color restored. This is the reliable, localized result
the diffusion editor can't produce on its own (see Aurora GAP-001).

Usage:
  python colorsplash_mask.py --video in.mp4 --phrase "woman" --out out.mp4
"""

import argparse

import imageio.v2 as imageio
import numpy as np
import torch
from PIL import Image
from transformers import (
    AutoModelForZeroShotObjectDetection,
    AutoProcessor,
    SamModel,
    SamProcessor,
)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--phrase", default="woman")
    ap.add_argument("--out", default="colorsplash.mp4")
    ap.add_argument("--dino", default="IDEA-Research/grounding-dino-base")
    ap.add_argument("--sam", default="facebook/sam-vit-base")
    ap.add_argument("--box_threshold", type=float, default=0.30)
    ap.add_argument("--feather", type=int, default=3, help="mask edge blur (px)")
    args = ap.parse_args()

    print(f"[splash] device={DEVICE} phrase={args.phrase!r}", flush=True)
    dino_proc = AutoProcessor.from_pretrained(args.dino)
    dino = (
        AutoModelForZeroShotObjectDetection.from_pretrained(args.dino).to(DEVICE).eval()
    )
    sam_proc = SamProcessor.from_pretrained(args.sam)
    sam = SamModel.from_pretrained(args.sam).to(DEVICE).eval()

    reader = imageio.get_reader(args.video)
    fps = float(reader.get_meta_data().get("fps", 16))
    frames = [f for f in reader]
    reader.close()
    print(f"[splash] {len(frames)} frames @ {fps:.1f}fps", flush=True)

    text = args.phrase if args.phrase.endswith(".") else args.phrase + "."
    out_frames = []
    last_mask = None

    for i, frame in enumerate(frames):
        img = Image.fromarray(frame).convert("RGB")
        W, H = img.size

        # 1) GroundingDINO: locate the subject -> best box
        d_in = dino_proc(images=img, text=text, return_tensors="pt").to(DEVICE)
        with torch.no_grad():
            d_out = dino(**d_in)
        # transformers >=4.4x renamed `box_threshold` -> `threshold`.
        try:
            res = dino_proc.post_process_grounded_object_detection(
                d_out,
                d_in["input_ids"],
                threshold=args.box_threshold,
                text_threshold=0.25,
                target_sizes=[(H, W)],
            )[0]
        except TypeError:
            res = dino_proc.post_process_grounded_object_detection(
                d_out,
                d_in["input_ids"],
                box_threshold=args.box_threshold,
                text_threshold=0.25,
                target_sizes=[(H, W)],
            )[0]

        mask = None
        if len(res["boxes"]) > 0:
            best = int(torch.argmax(res["scores"]))
            box = [float(v) for v in res["boxes"][best].tolist()]
            # 2) SAM: box -> precise mask
            s_in = sam_proc(img, input_boxes=[[box]], return_tensors="pt").to(DEVICE)
            with torch.no_grad():
                s_out = sam(**s_in)
            masks = sam_proc.image_processor.post_process_masks(
                s_out.pred_masks.cpu(),
                s_in["original_sizes"].cpu(),
                s_in["reshaped_input_sizes"].cpu(),
            )[0][0]
            iou = s_out.iou_scores.cpu()[0, 0]
            mask = masks[int(torch.argmax(iou))].numpy().astype(np.uint8) * 255

        if mask is None:
            mask = last_mask if last_mask is not None else np.zeros((H, W), np.uint8)
        else:
            last_mask = mask

        # 3) Composite: grayscale everywhere, restore color inside the mask
        arr = np.asarray(img).astype(np.float32)
        gray = arr.mean(axis=2, keepdims=True).repeat(3, axis=2)
        m = mask.astype(np.float32) / 255.0
        if args.feather > 0:
            from PIL import ImageFilter

            m_img = Image.fromarray((m * 255).astype(np.uint8)).filter(
                ImageFilter.GaussianBlur(args.feather)
            )
            m = np.asarray(m_img).astype(np.float32) / 255.0
        m = m[:, :, None]
        comp = (arr * m + gray * (1.0 - m)).clip(0, 255).astype(np.uint8)
        out_frames.append(comp)
        if (i + 1) % 16 == 0:
            print(f"[splash]  {i + 1}/{len(frames)} frames", flush=True)

    imageio.mimsave(args.out, out_frames, fps=fps, quality=8)
    print(f"[splash] DONE -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
