# Aurora overlay

These files are **additions to the upstream [Aurora](https://github.com/yeates/Aurora)
repo**, not a fork. `scripts/setup.sh` clones Aurora and copies these on top, so
they sit in the right places inside the Aurora working tree.

| File | Goes to (in Aurora repo) | What it is |
|---|---|---|
| `aurora/agent_api.py` | `aurora/agent_api.py` | Hosted-planner backends behind the same interface as Aurora's `aurora/agent.py`. Includes `BedrockAgent` (Amazon Bedrock Nova Pro via the Converse API) plus OpenAI and Gemini backends. |
| `run_single_sample.py` | `run_single_sample.py` (repo root) | Editor-only inference on one clip. |
| `batch_ad_edits.py` | `batch_ad_edits.py` (repo root) | Runs the whole ad prompt set through the editor in one pipeline load. |

Both scripts resolve the Aurora repo via the `AURORA_DIR` environment variable
(default: the script's own directory), so no paths are hardcoded.

## Running the Bedrock planner directly

```bash
cd Aurora
AWS_REGION=us-east-2 python -m aurora.agent_api \
  --backend bedrock --model us.amazon.nova-pro-v1:0 \
  --custom_cases_jsonl samples/ad_cases_nova_h2h.jsonl \
  --disable_image_search \
  --out_dir outputs/agent_nova
```

This writes `agent_pipeline_records.jsonl` in the same format Aurora's native
agent produces, so `aurora.editor_bridge_video` renders it unchanged.

Requires `boto3` and Amazon Bedrock **Nova Pro** model access in the chosen region.

Aurora is MIT-licensed; these overlay files are released under the same terms.
