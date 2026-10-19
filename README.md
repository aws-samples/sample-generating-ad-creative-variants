# Generating Ad Creative Variants with AI Video Editing on AWS

Turn a single product shoot into many on-brand ad variations — different colors, seasons, times of
day, and brand looks — using the open-source [Aurora](https://github.com/yeates/Aurora) video
editor, with a **swappable planning agent** (Amazon Bedrock Nova, Aurora's native Qwen3-VL, or
none). Ships as a **deployable, destroyable AWS pipeline**: GPU rendering runs on **AWS Batch** and
scales to zero when idle, and the whole stack is AWS CDK behind a gated CI/CD pipeline.

## How it works

A request carries a source video and a plain-English brief. A **VLM planner** turns the brief into a
small JSON editing instruction, and the **Aurora editor** (WAN2.2-TI2V-5B + WAN2.2 VAE +
Qwen3.5-4B) renders the edited clip. The planner is chosen per request via a `model` field:

- `none` (default) — editor only, prompt used verbatim
- `qwen` — Aurora's native Qwen3-VL-8B agent, on the GPU worker
- `us.amazon.nova-pro-v1:0` / `us.amazon.nova-lite-v1:0` — Amazon Bedrock Nova (managed, no GPU)

See the architecture diagram under [`docs/`](docs/aurora-video-editing-pipeline.png) and
[`infra/cdk/README.md`](infra/cdk/README.md) for the full pipeline.

## Repository layout

| Path | What it is |
|---|---|
| [`infra/cdk/`](infra/cdk/README.md) | The deployable AWS pipeline — CDK app (Security / Data / Inference / Ingestion stacks) + CI/CD |
| [`infra/docker/worker/`](infra/docker/worker/) | The GPU worker container (Aurora runtime + Batch entrypoint) |
| [`aurora-overlay/`](aurora-overlay/) | Files copied on top of the upstream Aurora clone (e.g. the Bedrock planner backend) |
| [`experiments/`](experiments/) | Prompt sets and edit cases used to generate the sample variants |
| [`scripts/`](scripts/) | Set up Aurora locally, fetch source clips, reproduce runs |
| [`tools/`](tools/) | Standalone helper scripts (keyframe extraction, color-splash masking) |
| [`docs/`](docs/) | Rendered architecture diagram (`.png`) |

## Do I need the whole Aurora repo?

**No.** Aurora is MIT-licensed, but instead of vendoring a fork we clone it from upstream at a
pinned commit and copy our additions (`aurora-overlay/`) on top — both in the worker image and in
the local setup script. The only code we ship is what we authored.

## Deploy the AWS pipeline

The pipeline is an AWS CDK app deployed through a gated CI/CD pipeline (AWS CodePipeline). GPU
rendering runs on AWS Batch and scales to zero when idle — there's no separate "turn on the GPU"
step.

```bash
cd infra/cdk

make setup                 # one-time: create venv + install dev deps
# set CDK_DEPLOY_ACCOUNT / CDK_DEPLOY_REGION in infra/cdk/.env (see .env.example)
export AWS_PROFILE=<you>

make deploy                # run gates, then deploy/update the pipeline stack
make push                  # push the source so the pipeline runs (gates -> deploy stacks -> build worker image)

make destroy               # tear everything down
```

`make deploy` bootstraps/updates the pipeline; `make push` triggers a pipeline run that deploys the
four stacks and builds/pushes the GPU worker image. In dev, all data resources are destroyable, so
teardown leaves nothing behind. Full details — stacks, CI/CD gates, and EFS model-weight seeding —
are in [`infra/cdk/README.md`](infra/cdk/README.md).

## Run a job

Once deployed, submit work two ways (both land on the same AWS Batch queue).

**Single request** — upload a clip to the landing bucket, then call the API (`POST /jobs`, API-key
protected):

```bash
awscurl --service execute-api --region <region> \
  -X POST "https://<api-id>.execute-api.<region>.amazonaws.com/prod/jobs" \
  -H "x-api-key: <api-key>" -H "Content-Type: application/json" \
  -d '{"video_name": "clip.mp4", "prompt": "warmer golden hour lighting", "model": "none"}'
```

Poll `GET /jobs/{job_id}`; the result lands at `s3://<results-bucket>/outputs/<job_id>.mp4`.

**Batch request** — drop a CSV into the landing bucket's `batch/` prefix; an EventBridge rule fans
it out to one Batch job per row:

```csv
video_name,image_name,prompt,model,seed,num_frames,height,width
clip.mp4,,warmer golden hour lighting,none,42,81,480,832
clip.mp4,,cool moody blue night grade,none,7,81,480,832
```

```bash
aws s3 cp edits.csv s3://<landing-bucket>/batch/edits.csv
```

## Reproduce the results locally (optional)

To run Aurora on your own GPU instead of the AWS pipeline:

```bash
./scripts/setup.sh              # clone Aurora (pinned) + apply overlay + build env + download weights (needs a CUDA GPU)
./scripts/download_sources.sh   # download the 4 free Mixkit source clips, trimmed to 9s
AWS_REGION=<region> ./scripts/run_experiments.sh   # plan + render with both planners (Nova needs Bedrock access)
```

## What is NOT in this repo

- **Model weights** (WAN2.2-TI2V-5B, Qwen3.5-4B, the Aurora editor/agent checkpoints). They
  download from Hugging Face during setup / EFS seeding, each under its own license.
- **The upstream Aurora source.** It's cloned at a pinned commit by the worker build and
  `scripts/setup.sh`.
- **Result/source videos.** No `.mp4` files are committed; they're reproducible via
  `scripts/download_sources.sh` + `scripts/run_experiments.sh`.

## Attribution & licensing

- Aurora — *Unified Video Editing with a Tool-Using Agent*
  (arXiv:2605.18748; https://github.com/yeates/Aurora), MIT License.
- Source clips: free, commercial-use stock from [Mixkit](https://mixkit.co) (Mixkit Free License).
  Note Mixkit does not clear third-party elements (recognizable people, brands, trade dress) that
  appear in a clip — clear those rights before publishing edited footage in a real campaign.
- This repository's own code is released under the Apache License 2.0 (see [`LICENSE`](LICENSE)).
