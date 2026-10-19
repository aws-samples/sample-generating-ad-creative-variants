#!/usr/bin/env bash
# Set up Aurora and overlay our additions. We do NOT vendor the Aurora repo;
# we clone it from upstream at a pinned commit and copy our files on top.
#
# Usage: ./scripts/setup.sh [target_dir]
#   target_dir defaults to ./Aurora
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AURORA_URL="${AURORA_URL:-https://github.com/yeates/Aurora.git}"
AURORA_REF="${AURORA_REF:-main}"     # pin to a tested commit/tag for reproducibility
TARGET="${1:-$REPO_ROOT/Aurora}"

echo ">> Cloning Aurora ($AURORA_REF) into $TARGET"
if [ ! -d "$TARGET/.git" ]; then
  git clone "$AURORA_URL" "$TARGET"
fi
git -C "$TARGET" fetch --all --tags
git -C "$TARGET" checkout "$AURORA_REF"

echo ">> Applying overlay (our planner backends + experiment scripts)"
cp -v "$REPO_ROOT/aurora-overlay/aurora/agent_api.py"   "$TARGET/aurora/agent_api.py"
cp -v "$REPO_ROOT/aurora-overlay/run_single_sample.py"  "$TARGET/run_single_sample.py"
cp -v "$REPO_ROOT/aurora-overlay/batch_ad_edits.py"     "$TARGET/batch_ad_edits.py"

echo ">> Linking experiment data into the Aurora working dir"
mkdir -p "$TARGET/samples"
cp -v "$REPO_ROOT/experiments/ad_prompts.json"        "$TARGET/samples/ad_prompts.json"
cp -v "$REPO_ROOT/experiments/ad_cases_nova.jsonl"     "$TARGET/samples/ad_cases_nova.jsonl"
cp -v "$REPO_ROOT/experiments/ad_cases_nova_h2h.jsonl" "$TARGET/samples/ad_cases_nova_h2h.jsonl"

echo ">> Building the Aurora environment (delegates to Aurora's own installer)"
echo "   This installs PyTorch/cu124, flash-attn, etc. See Aurora/README.md."
( cd "$TARGET" && bash setup_env.sh )

echo ">> Downloading model weights (WAN2.2-TI2V-5B, Qwen3.5-4B, Aurora editor, agent)"
( cd "$TARGET" && python -m evaluation.model_download --what all )

echo ">> Done. Aurora is set up at: $TARGET"
echo "   Next: ./scripts/download_sources.sh \"$TARGET\"  then  ./scripts/run_experiments.sh \"$TARGET\""
