"""Batch Aurora editor-only inference over samples/ad_prompts.json.

Loads the pipeline ONCE and renders every clip/variation, writing each to a
separate file under outputs/ad/ and appending a row to outputs/ad/results.jsonl.

Usage:
  python batch_ad_edits.py [--prompts samples/ad_prompts.json] [--cfg_scale 2.0]
                           [--skip_substr "Selective color"]
"""

import argparse
import json
import os
import re
import sys
import time
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


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", default="samples/ad_prompts.json")
    ap.add_argument("--out_dir", default="outputs/ad")
    ap.add_argument("--cfg_scale", type=float, default=2.0)
    ap.add_argument("--num_frames", type=int, default=81)
    ap.add_argument("--save_frames", type=int, default=64)
    ap.add_argument("--max_pixels", type=int, default=480 * 832)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fps", type=float, default=16.0)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument(
        "--skip_substr",
        default="Selective color",
        help="skip variations whose use_case contains this substring",
    )
    args = ap.parse_args()

    cfg = json.loads(Path(args.prompts).read_text())
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"

    paths = default_paths()
    ckpt = resolve_editor_ckpt(None, paths=paths)
    print(f"[batch] editor ckpt: {ckpt}")
    pipe = load_v2_pipeline(ckpt, device=args.device, ref_max_items=8, paths=paths)
    print("[batch] pipeline loaded once; rendering all variations\n")

    jobs = []
    for clip in cfg["clips"]:
        for v in clip["variations"]:
            if args.skip_substr and args.skip_substr.lower() in v["use_case"].lower():
                print(f"[batch] skip {clip['id']} / {v['use_case']}")
                continue
            jobs.append((clip, v))

    print(f"[batch] {len(jobs)} jobs to run\n")
    for i, (clip, v) in enumerate(jobs, 1):
        video_path = clip["video"]
        image_cfg = float(v.get("image_cfg", 1.0))
        out_path = out_dir / f"{clip['id']}_{slug(v['use_case'])}.mp4"
        print(
            f"\n===== [{i}/{len(jobs)}] clip {clip['id']} | {v['use_case']} "
            f"| image_cfg={image_cfg} ====="
        )
        print(f"prompt: {v['prompt']}")

        num_frames = align_frames(args.num_frames)
        src = VideoData(video_path, length=num_frames, max_pixels=args.max_pixels)
        gen_h, gen_w = src.height, src.width
        raw = len(src)
        num_frames = align_frames(raw)
        if num_frames != raw:
            src = VideoData(video_path, length=num_frames, max_pixels=args.max_pixels)

        t0 = time.time()
        out = pipe(
            prompt=v["prompt"],
            negative_prompt="",
            source_input=src,
            src_video=src,
            ref_image=None,
            height=gen_h,
            width=gen_w,
            num_frames=num_frames,
            seed=args.seed,
            cfg_scale=args.cfg_scale,
            image_cfg_scale=image_cfg,
            fallback_to_two_pass_cfg=True,
            tiled=True,
        )
        out = list(out)
        if args.save_frames > 0 and args.save_frames < len(out):
            out = out[: args.save_frames]
        save_video(out, str(out_path), fps=args.fps, quality=5)
        dt = time.time() - t0
        print(f"[batch] DONE -> {out_path} ({len(out)} frames) in {dt:.0f}s")

        row = {
            "clip_id": clip["id"],
            "genre": clip.get("genre"),
            "use_case": v["use_case"],
            "subtask": v.get("subtask"),
            "source": video_path,
            "output": str(out_path),
            "prompt": v["prompt"],
            "cfg_scale": args.cfg_scale,
            "image_cfg_scale": image_cfg,
            "resolution": f"{gen_w}x{gen_h}",
            "num_frames": num_frames,
            "save_frames": len(out),
            "seed": args.seed,
            "fps": args.fps,
            "runtime_s": round(dt, 1),
        }
        with results_path.open("a") as f:
            f.write(json.dumps(row) + "\n")

    print(f"\n[batch] all done. results -> {results_path}")


if __name__ == "__main__":
    main()
