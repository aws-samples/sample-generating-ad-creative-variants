#!/usr/bin/env python3
"""AWS Batch worker entrypoint: run one Aurora editor job end to end.

Reads its parameters from environment variables (set by the submitting Lambda
via containerOverrides), downloads the source from the staging bucket, runs the
Aurora editor with weights mounted from EFS (/models), uploads the result to the
results bucket, and updates the job status in DynamoDB.

Environment (from the Batch submit):
  JOB_ID, STAGING_BUCKET, RESULTS_BUCKET, JOB_TABLE,
  VIDEO_KEY, IMAGE_KEY, OUTPUT_KEY, PROMPT,
  SEED, NUM_FRAMES, HEIGHT, WIDTH
  MODELS_DIR (EFS mount, default /models), AURORA_DIR
"""

import datetime
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

import boto3

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")

# Bedrock client is created lazily only when a Nova plan is requested.
_PLANNER_SYSTEM = (
    "You turn a short advertising edit request into a single, clean, specific "
    "editor instruction. Reply with ONLY the refined instruction text, no "
    "prose, no JSON, no quotes."
)

JOB_ID = os.environ["JOB_ID"]
STAGING = os.environ["STAGING_BUCKET"]
RESULTS = os.environ["RESULTS_BUCKET"]
TABLE = os.environ["JOB_TABLE"]
VIDEO_KEY = os.environ["VIDEO_KEY"]
IMAGE_KEY = os.environ.get("IMAGE_KEY") or None
OUTPUT_KEY = os.environ["OUTPUT_KEY"]
PROMPT = os.environ["PROMPT"]
# MODEL is a Bedrock model id used to refine the prompt, or "none" (editor-only).
MODEL = os.environ.get("MODEL", "none")
SEED = os.environ.get("SEED", "42")
NUM_FRAMES = os.environ.get("NUM_FRAMES", "81")
HEIGHT = os.environ.get("HEIGHT", "480")
WIDTH = os.environ.get("WIDTH", "832")

AURORA_DIR = os.environ.get("AURORA_DIR", "/opt/aurora/Aurora")


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def set_status(status, **extra):
    # Every attribute name goes through an ExpressionAttributeNames placeholder
    # so reserved words (e.g. "status", "error") don't break the update.
    expr = "SET #s = :s, #u = :u"
    names = {"#s": "status", "#u": "updated_at"}
    values = {":s": {"S": status}, ":u": {"S": _now()}}
    for i, (k, v) in enumerate(extra.items()):
        names[f"#k{i}"] = k
        expr += f", #k{i} = :e{i}"
        values[f":e{i}"] = {"S": str(v)}
    ddb.update_item(
        TableName=TABLE,
        Key={"job_id": {"S": JOB_ID}},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def refine_prompt(prompt, model_id):
    """Use a Bedrock model to turn a brief into a clean editor instruction.

    Returns the refined instruction, or the original prompt if refinement is
    unavailable/empty (so a planning hiccup never blocks the render).
    """
    bedrock = boto3.client("bedrock-runtime")
    resp = bedrock.converse(
        modelId=model_id,
        system=[{"text": _PLANNER_SYSTEM}],
        messages=[{"role": "user", "content": [{"text": f"Edit request: {prompt}"}]}],
        inferenceConfig={"maxTokens": 256, "temperature": 0.4},
    )
    text = "".join(
        p.get("text", "") for p in resp["output"]["message"]["content"]
    ).strip()
    return text or prompt


def run_editor(src, prompt, out):
    """Editor-only render of a final editor-facing prompt (run_single_sample)."""
    models_dir = os.environ.get("MODELS_DIR", "/models")

    # Log EFS contents for debugging checkpoint issues
    print(f"DEBUG: models_dir={models_dir}", flush=True)
    try:
        top_level = os.listdir(models_dir)
        print(f"DEBUG: {models_dir}/ -> {sorted(top_level)}", flush=True)
        editor_dir = os.path.join(models_dir, "aurora_video_editor")
        if os.path.isdir(editor_dir):
            print(f"DEBUG: {editor_dir}/ -> {os.listdir(editor_dir)}", flush=True)
    except OSError as e:
        print(f"DEBUG: listdir failed: {e}", flush=True)

    # Resolve checkpoint: prefer flat path, fall back to aurora_video_editor/
    ckpt = os.path.join(models_dir, "aurora_editor.safetensors")
    if not os.path.isfile(ckpt):
        alt = os.path.join(models_dir, "aurora_video_editor", "model.safetensors")
        print(f"WARN: {ckpt} not found, trying {alt}", flush=True)
        if os.path.isfile(alt):
            ckpt = alt
        else:
            print(f"ERROR: neither checkpoint path exists", flush=True)

    # Single-token --flag=value form: argparse takes everything after "=" as the
    # value, so a caller-supplied prompt that starts with "-" is passed through
    # as text instead of being misread as another option.
    cmd = [
        sys.executable,
        os.path.join(AURORA_DIR, "run_single_sample.py"),
        f"--video={src}",
        f"--prompt={prompt}",
        f"--out={out}",
        f"--ckpt={ckpt}",
        f"--seed={SEED}",
        f"--num_frames={NUM_FRAMES}",
    ]
    print("RENDER:", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=AURORA_DIR, check=True)


def run_qwen_agent(src, prompt, workdir, out):
    """Aurora's native Qwen3-VL agent path: plan (local VLM) then render.

    Two steps: aurora.agent produces a structured plan record, then
    editor_bridge_video renders it. Web image search + masks are disabled to
    keep the worker self-contained (no Serper / GroundingDINO+SAM).
    """
    cases = os.path.join(workdir, "cases.jsonl")
    with open(cases, "w", encoding="utf-8") as f:
        f.write(
            json.dumps({"bench_id": JOB_ID, "prompt": prompt, "video_path": src}) + "\n"
        )

    agent_out = os.path.join(workdir, "agent_out")
    video_out = os.path.join(workdir, "video_out")

    # 1) Plan with the native Qwen3-VL agent (base + aurora_agent_vlm LoRA from EFS).
    subprocess.run(
        [
            sys.executable,
            "-m",
            "aurora.agent",
            "--custom_cases_jsonl",
            cases,
            "--custom_only",
            "--disable_image_search",
            "--mask_backend",
            "none",
            "--out_dir",
            agent_out,
        ],
        cwd=AURORA_DIR,
        check=True,
    )
    records = os.path.join(agent_out, "agent_pipeline_records.jsonl")

    # 2) Render the plan with the editor.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "aurora.editor_bridge_video",
            "--records_jsonl",
            records,
            "--out_dir",
            video_out,
            "--num_frames",
            NUM_FRAMES,
            "--seed",
            SEED,
        ],
        cwd=AURORA_DIR,
        check=True,
    )

    produced = sorted(
        glob.glob(os.path.join(video_out, "**", "*.mp4"), recursive=True),
        key=os.path.getmtime,
    )
    if not produced:
        raise RuntimeError("Qwen agent path produced no output video")
    shutil.copy(produced[-1], out)


def run():
    # Use a private, unpredictable working directory rather than a hardcoded
    # /tmp path (avoids symlink/predictable-path issues; bandit B108).
    workdir = tempfile.mkdtemp(prefix="aurora_job_")
    src = os.path.join(workdir, "source.mp4")
    out = os.path.join(workdir, "out.mp4")

    try:
        s3.download_file(STAGING, VIDEO_KEY, src)

        # Model selection:
        #   "none"        -> editor-only, prompt used verbatim
        #   "qwen"        -> Aurora's native Qwen3-VL agent (local) then render
        #   <bedrock id>  -> refine the brief via Bedrock, then editor render
        if MODEL == "qwen":
            print("PLAN+RENDER: native Qwen3-VL agent path", flush=True)
            run_qwen_agent(src, PROMPT, workdir, out)
        else:
            prompt = PROMPT
            if MODEL and MODEL != "none":
                print(f"PLAN: refining prompt via {MODEL}", flush=True)
                prompt = refine_prompt(PROMPT, MODEL)
                print(f"PLAN: refined -> {prompt!r}", flush=True)
            run_editor(src, prompt, out)

        s3.upload_file(out, RESULTS, OUTPUT_KEY)
        return OUTPUT_KEY
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    set_status("RUNNING", batch_started_at=_now())
    try:
        output_key = run()
        set_status("SUCCEEDED", output_key=output_key)
        print(f"done: {JOB_ID} -> s3://{RESULTS}/{output_key}", flush=True)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        set_status("FAILED", error=str(e)[:900])
        sys.exit(1)


if __name__ == "__main__":
    main()
