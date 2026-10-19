#!/usr/bin/env bash
# Reproduce the blog's variants: plan each edit with both planners (Aurora's
# native Qwen3-VL agent and Amazon Bedrock Nova Pro), then render with the editor.
#
# Usage: ./scripts/run_experiments.sh [aurora_dir]
# Env:
#   AWS_REGION   region for Bedrock (default us-east-2; needs Nova Pro access)
#   CASES        case file (default samples/ad_cases_nova_h2h.jsonl)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AURORA_DIR="${1:-$REPO_ROOT/Aurora}"
AWS_REGION="${AWS_REGION:-us-east-2}"
CASES="${CASES:-samples/ad_cases_nova_h2h.jsonl}"
CKPT="models/aurora_editor.safetensors"

cd "$AURORA_DIR"

echo "============================================================"
echo " 1) Native Qwen3-VL agent: plan -> render"
echo "============================================================"
python -m aurora.agent \
  --custom_cases_jsonl "$CASES" \
  --custom_only \
  --disable_image_search \
  --out_dir outputs/agent_qwen
python -m aurora.editor_bridge_video \
  --records_jsonl outputs/agent_qwen/agent_pipeline_records.jsonl \
  --ckpt "$CKPT" \
  --out_dir outputs/qwen \
  --num_frames 81 --save_frames 64

echo "============================================================"
echo " 2) Amazon Bedrock Nova Pro: plan -> render"
echo "============================================================"
AWS_REGION="$AWS_REGION" python -m aurora.agent_api \
  --backend bedrock --model us.amazon.nova-pro-v1:0 \
  --custom_cases_jsonl "$CASES" \
  --disable_image_search \
  --out_dir outputs/agent_nova
python -m aurora.editor_bridge_video \
  --records_jsonl outputs/agent_nova/agent_pipeline_records.jsonl \
  --ckpt "$CKPT" \
  --out_dir outputs/nova \
  --num_frames 81 --save_frames 64

echo ">> Done. Variants in $AURORA_DIR/outputs/qwen and outputs/nova"
echo "   (Editor-only baseline alternative: python batch_ad_edits.py)"
