"""Aurora single-sample editor inference (editor-only path, no agent).

Loads the WAN2.2-TI2V-5B editor + WAN VAE + Qwen3.5-4B via load_v2_pipeline,
restores aurora_editor.safetensors, and runs one source-conditioned (s2v) edit.

Usage:
  python run_single_sample.py \
    --video samples/source.mp4 \
    --prompt "turn it into a snowy winter scene with falling snow" \
    --out outputs/edited.mp4
"""

import argparse
import os
import sys
from pathlib import Path

REPO = Path(os.environ.get("AURORA_DIR", Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))

from diffsynth import VideoData, save_video
from evaluation.pipeline_loader import default_paths, load_v2_pipeline
from evaluation.model_download import resolve_editor_ckpt

TIME_DIV_FACTOR, TIME_DIV_REMAINDER = 4, 1


def align_frames(n, factor=TIME_DIV_FACTOR, remainder=TIME_DIV_REMAINDER):
    while n > 1 and n % factor != remainder:
        n -= 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", default="outputs/edited.mp4")
    ap.add_argument("--ckpt", default=None, help="editor ckpt (default auto)")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--num_frames", type=int, default=81)
    ap.add_argument("--save_frames", type=int, default=64)
    ap.add_argument("--max_pixels", type=int, default=480 * 832)
    ap.add_argument("--cfg_scale", type=float, default=2.0)
    ap.add_argument("--image_cfg_scale", type=float, default=1.0)
    ap.add_argument("--fallback_to_two_pass_cfg", action="store_true", default=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fps", type=float, default=16.0)
    args = ap.parse_args()

    paths = default_paths()
    ckpt = resolve_editor_ckpt(args.ckpt, paths=paths)
    print(f"[infer] editor ckpt: {ckpt}")

    pipe = load_v2_pipeline(ckpt, device=args.device, ref_max_items=8, paths=paths)
    print("[infer] pipeline loaded")

    num_frames = align_frames(args.num_frames)
    src = VideoData(args.video, length=num_frames, max_pixels=args.max_pixels)
    gen_h, gen_w = src.height, src.width
    raw = len(src)
    num_frames = align_frames(raw)
    if num_frames != raw:
        src = VideoData(args.video, length=num_frames, max_pixels=args.max_pixels)
    print(
        f"[infer] source {gen_w}x{gen_h}, {num_frames} frames; prompt: {args.prompt!r}"
    )

    video = pipe(
        prompt=args.prompt,
        negative_prompt="",
        source_input=src,
        src_video=src,
        ref_image=None,
        height=gen_h,
        width=gen_w,
        num_frames=num_frames,
        seed=args.seed,
        cfg_scale=args.cfg_scale,
        image_cfg_scale=args.image_cfg_scale,
        fallback_to_two_pass_cfg=args.fallback_to_two_pass_cfg,
        tiled=True,
    )
    video = list(video)
    if args.save_frames > 0 and args.save_frames < len(video):
        video = video[: args.save_frames]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_video(video, str(out_path), fps=args.fps, quality=5)
    print(f"[infer] DONE -> {out_path} ({len(video)} frames @ {args.fps}fps)")


if __name__ == "__main__":
    main()
