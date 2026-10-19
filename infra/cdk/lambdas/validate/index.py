"""Validate + preprocess handler (API Gateway proxy).

Routes:
  POST /jobs             submit a new edit job
  GET  /jobs/{job_id}    fetch job status

On POST it:
  1. parses and validates the request body (params + bounds)
  2. checks the uploaded video/image exist in the landing bucket and have an
     allowed format/extension
  3. copies validated assets from landing -> staging (ready to process)
  4. writes a job record to DynamoDB (status=VALIDATED)
  5. submits an AWS Batch job, then marks status=SUBMITTED
  6. returns the job_id and initial status

The Batch worker later flips status to RUNNING/SUCCEEDED/FAILED.
"""
import datetime
import json
import os
import re
import uuid

import boto3

s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")
batch = boto3.client("batch")
ssm = boto3.client("ssm")

LANDING = os.environ["LANDING_BUCKET"]
STAGING = os.environ["STAGING_BUCKET"]
RESULTS = os.environ["RESULTS_BUCKET"]
TABLE = os.environ["JOB_TABLE"]


def _ssm_get(name):
    return ssm.get_parameter(Name=name)["Parameter"]["Value"]


QUEUE_ARN = _ssm_get(os.environ["JOB_QUEUE_ARN_SSM"])
JOBDEF_ARN = _ssm_get(os.environ["JOB_DEFINITION_ARN_SSM"])

MAX_FRAMES = int(os.environ["MAX_NUM_FRAMES"])
MIN_FRAMES = int(os.environ["MIN_NUM_FRAMES"])
MAX_DIM = int(os.environ["MAX_DIMENSION"])
VIDEO_EXT = os.environ["ALLOWED_VIDEO_EXT"].split(",")
IMAGE_EXT = os.environ["ALLOWED_IMAGE_EXT"].split(",")
# Bedrock model ids selectable for planning; "none" means editor-only.
ALLOWED_MODELS = [m for m in os.environ.get("ALLOWED_MODELS", "").split(",") if m]

MAX_PROMPT_LENGTH = 10_000

SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9._-]+$")

REQUIRED = ["video_name", "prompt"]


def sanitize_key(key: str) -> str:
    """Validate S3 key components - reject path traversal."""
    parts = key.split("/")
    for part in parts:
        if part in ("", ".", "..") or not SAFE_FILENAME.match(part):
            raise ValueError(f"Invalid key component: {part!r}")
    return key


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _now():
    return (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat() + "Z"
    )


def _ext(key):
    return os.path.splitext(key)[1].lower()


_NOT_FOUND_CODES = ("404", "NoSuchKey", "NotFound")


def _head(bucket, key):
    """Return object metadata, or None if the object genuinely does not exist.

    Only a 404-class error means "not found". Other client errors (403
    permission denied, throttling, etc.) are re-raised so they are handled by
    the top-level guard and logged, rather than being silently misreported as
    a missing object.
    """
    try:
        return s3.head_object(Bucket=bucket, Key=key)
    except s3.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
            return None
        raise


def validate_request(req):
    """Return (params, error). params is None when invalid."""
    for k in REQUIRED:
        if not req.get(k):
            return None, f"missing required field: {k}"

    prompt = req["prompt"]
    if len(prompt) > MAX_PROMPT_LENGTH:
        return None, f"prompt exceeds {MAX_PROMPT_LENGTH} characters"

    video_name = req["video_name"]
    image_name = req.get("image_name")

    if _ext(video_name) not in VIDEO_EXT:
        return None, f"video must be one of {VIDEO_EXT}, got {_ext(video_name)}"
    if image_name and _ext(image_name) not in IMAGE_EXT:
        return None, f"image must be one of {IMAGE_EXT}, got {_ext(image_name)}"

    # Planner model: "none"/omitted = editor-only; otherwise a Bedrock model id
    # from the allowlist that the worker uses to refine the prompt.
    model = str(req.get("model") or "none")
    if model != "none" and model not in ALLOWED_MODELS:
        return None, f"model must be 'none' or one of {ALLOWED_MODELS}, got {model!r}"

    # Numeric params with bounds.
    try:
        seed = int(req.get("seed", 42))
        num_frames = int(req.get("num_frames", 81))
        height = int(req.get("height", 480))
        width = int(req.get("width", 832))
    except (TypeError, ValueError):
        return None, "seed/num_frames/height/width must be integers"

    if not (MIN_FRAMES <= num_frames <= MAX_FRAMES):
        return None, f"num_frames must be in [{MIN_FRAMES}, {MAX_FRAMES}]"
    if not (0 < height <= MAX_DIM) or not (0 < width <= MAX_DIM):
        return None, f"height/width must be in (0, {MAX_DIM}]"

    return {
        "video_name": video_name,
        "image_name": image_name,
        "prompt": req["prompt"],
        "model": model,
        "seed": seed,
        "num_frames": num_frames,
        "height": height,
        "width": width,
    }, None


def check_and_stage(params):
    """Confirm assets exist in landing + copy to staging. Returns (keys, error)."""
    video_src = params["video_name"]
    try:
        sanitize_key(video_src)
    except ValueError as e:
        return None, f"invalid video_name: {e}"
    if _head(LANDING, video_src) is None:
        return None, f"video not found in landing bucket: {video_src}"

    keys = {}
    video_dst = f"ready/{video_src}"
    s3.copy_object(
        Bucket=STAGING,
        Key=video_dst,
        CopySource={"Bucket": LANDING, "Key": video_src},
    )
    keys["video_key"] = video_dst

    if params["image_name"]:
        image_src = params["image_name"]
        try:
            sanitize_key(image_src)
        except ValueError as e:
            return None, f"invalid image_name: {e}"
        if _head(LANDING, image_src) is None:
            return None, f"image not found in landing bucket: {image_src}"
        image_dst = f"ready/{image_src}"
        s3.copy_object(
            Bucket=STAGING,
            Key=image_dst,
            CopySource={"Bucket": LANDING, "Key": image_src},
        )
        keys["image_key"] = image_dst

    return keys, None


def put_job(job_id, params, keys, status):
    output_key = f"outputs/{job_id}.mp4"
    item = {
        "job_id": {"S": job_id},
        "status": {"S": status},
        "created_at": {"S": _now()},
        "updated_at": {"S": _now()},
        "video_key": {"S": keys["video_key"]},
        "prompt": {"S": params["prompt"]},
        "output_key": {"S": output_key},
        "params": {"M": {
            "seed": {"N": str(params["seed"])},
            "num_frames": {"N": str(params["num_frames"])},
            "height": {"N": str(params["height"])},
            "width": {"N": str(params["width"])},
        }},
    }
    if keys.get("image_key"):
        item["image_key"] = {"S": keys["image_key"]}
    ddb.put_item(TableName=TABLE, Item=item)
    return output_key


def submit_batch(job_id, params, keys, output_key):
    resp = batch.submit_job(
        jobName=f"aurora-{job_id}",
        jobQueue=QUEUE_ARN,
        jobDefinition=JOBDEF_ARN,
        containerOverrides={
            "environment": [
                {"name": "JOB_ID", "value": job_id},
                {"name": "STAGING_BUCKET", "value": STAGING},
                {"name": "RESULTS_BUCKET", "value": RESULTS},
                {"name": "JOB_TABLE", "value": TABLE},
                {"name": "VIDEO_KEY", "value": keys["video_key"]},
                {"name": "IMAGE_KEY", "value": keys.get("image_key", "")},
                {"name": "OUTPUT_KEY", "value": output_key},
                {"name": "PROMPT", "value": params["prompt"]},
                {"name": "MODEL", "value": params["model"]},
                {"name": "SEED", "value": str(params["seed"])},
                {"name": "NUM_FRAMES", "value": str(params["num_frames"])},
                {"name": "HEIGHT", "value": str(params["height"])},
                {"name": "WIDTH", "value": str(params["width"])},
            ]
        },
    )
    job_ref = resp.get("jobId")
    if not job_ref:
        # Defensive: SubmitJob should always return a jobId. Fail explicitly
        # (the caller logs and returns a generic error) rather than KeyError.
        raise RuntimeError("Batch SubmitJob returned no jobId")
    return job_ref


def handle_post(event):
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode()
    try:
        req = json.loads(body)
    except json.JSONDecodeError:
        return _resp(400, {"error": "body must be valid JSON"})

    params, err = validate_request(req)
    if err:
        return _resp(400, {"error": err})

    keys, err = check_and_stage(params)
    if err:
        return _resp(422, {"error": err})

    job_id = str(uuid.uuid4())
    output_key = put_job(job_id, params, keys, status="VALIDATED")

    try:
        batch_job_id = submit_batch(job_id, params, keys, output_key)
    except Exception as e:  # noqa: BLE001
        # Log the full error to CloudWatch; never return raw AWS error detail
        # (ARNs, account ids, internal messages) to the API caller.
        print(f"[submit_batch] job {job_id} failed: {e!r}", flush=True)
        ddb.update_item(
            TableName=TABLE,
            Key={"job_id": {"S": job_id}},
            UpdateExpression="SET #s = :s, #e = :e, updated_at = :u",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={
                ":s": {"S": "FAILED"},
                ":e": {"S": "job submission failed"},
                ":u": {"S": _now()},
            },
        )
        return _resp(502, {"job_id": job_id, "error": "failed to submit job"})

    ddb.update_item(
        TableName=TABLE,
        Key={"job_id": {"S": job_id}},
        UpdateExpression="SET #s = :s, batch_job_id = :b, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": {"S": "SUBMITTED"},
            ":b": {"S": batch_job_id},
            ":u": {"S": _now()},
        },
    )
    return _resp(200, {
        "job_id": job_id,
        "status": "SUBMITTED",
        "output_key": output_key,
    })


def handle_get(event):
    params = event.get("pathParameters") or {}
    job_id = params.get("job_id")
    if not job_id:
        return _resp(400, {"error": "job_id required"})
    item = ddb.get_item(TableName=TABLE, Key={"job_id": {"S": job_id}}).get("Item")
    if not item:
        return _resp(404, {"error": "job not found"})
    return _resp(200, {
        "job_id": job_id,
        "status": item["status"]["S"],
        "output_key": item.get("output_key", {}).get("S"),
        "error": item.get("error", {}).get("S"),
    })


def handler(event, context):
    try:
        method = event.get("httpMethod", "POST")
        if method == "GET":
            return handle_get(event)
        return handle_post(event)
    except Exception as e:  # noqa: BLE001
        # Catch-all: log detail to CloudWatch, return an opaque error to the
        # caller so internal AWS details are never exposed.
        print(f"[handler] unhandled error: {e!r}", flush=True)
        return _resp(500, {"error": "internal error"})
