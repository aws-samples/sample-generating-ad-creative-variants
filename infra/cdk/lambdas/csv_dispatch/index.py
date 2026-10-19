"""Async CSV dispatch handler (S3-triggered).

When a CSV is dropped into s3://<landing>/batch/*.csv, this parses each row and
submits an edit job (the same path the API uses). This is the bulk/offline
option: one CSV -> many jobs.

Expected CSV header (comma-separated):
  video_name,image_name,prompt,seed,num_frames,height,width

`image_name` may be blank. seed/num_frames/height/width fall back to defaults
when blank.
"""
import csv
import datetime
import io
import json
import os
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

MAX_CSV_SIZE = 10 * 1024 * 1024  # 10 MB

DEFAULTS = {"seed": 42, "num_frames": 81, "height": 480, "width": 832}


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def _ext(key):
    return os.path.splitext(key)[1].lower()


def _int(row, key):
    val = (row.get(key) or "").strip()
    return int(val) if val else DEFAULTS[key]


def validate_row(row):
    video = (row.get("video_name") or "").strip()
    prompt = (row.get("prompt") or "").strip()
    image = (row.get("image_name") or "").strip() or None
    model = (row.get("model") or "none").strip() or "none"
    if not video or not prompt:
        return None, "video_name and prompt are required"
    if _ext(video) not in VIDEO_EXT:
        return None, f"bad video ext {_ext(video)}"
    if image and _ext(image) not in IMAGE_EXT:
        return None, f"bad image ext {_ext(image)}"
    if model != "none" and model not in ALLOWED_MODELS:
        return None, f"model must be 'none' or one of {ALLOWED_MODELS}"
    try:
        seed = _int(row, "seed")
        num_frames = _int(row, "num_frames")
        height = _int(row, "height")
        width = _int(row, "width")
    except ValueError:
        return None, "numeric fields must be integers"
    if not (MIN_FRAMES <= num_frames <= MAX_FRAMES):
        return None, "num_frames out of range"
    if not (0 < height <= MAX_DIM) or not (0 < width <= MAX_DIM):
        return None, "height/width out of range"
    return {
        "video_name": video, "image_name": image, "prompt": prompt, "model": model,
        "seed": seed, "num_frames": num_frames, "height": height, "width": width,
    }, None


_NOT_FOUND_CODES = ("404", "NoSuchKey", "NotFound")


def _exists(bucket, key):
    """True if the object exists, False if it genuinely does not (404).

    Non-404 client errors (permissions, throttling) are re-raised so the
    caller's guard logs them instead of misreporting a missing object.
    """
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except s3.exceptions.ClientError as e:
        if e.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
            return False
        raise


def stage_and_submit(params):
    video_src = params["video_name"]
    if not _exists(LANDING, video_src):
        return None, f"video not found: {video_src}"

    video_dst = f"ready/{video_src}"
    s3.copy_object(Bucket=STAGING, Key=video_dst,
                   CopySource={"Bucket": LANDING, "Key": video_src})
    keys = {"video_key": video_dst}

    if params["image_name"]:
        image_src = params["image_name"]
        if not _exists(LANDING, image_src):
            return None, f"image not found: {image_src}"
        image_dst = f"ready/{image_src}"
        s3.copy_object(Bucket=STAGING, Key=image_dst,
                       CopySource={"Bucket": LANDING, "Key": image_src})
        keys["image_key"] = image_dst

    job_id = str(uuid.uuid4())
    output_key = f"outputs/{job_id}.mp4"

    item = {
        "job_id": {"S": job_id},
        "status": {"S": "VALIDATED"},
        "created_at": {"S": _now()},
        "updated_at": {"S": _now()},
        "video_key": {"S": keys["video_key"]},
        "prompt": {"S": params["prompt"]},
        "output_key": {"S": output_key},
        "source": {"S": "csv"},
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

    resp = batch.submit_job(
        jobName=f"aurora-{job_id}",
        jobQueue=QUEUE_ARN,
        jobDefinition=JOBDEF_ARN,
        containerOverrides={"environment": [
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
        ]},
    )
    job_ref = resp.get("jobId")
    if not job_ref:
        raise RuntimeError("Batch SubmitJob returned no jobId")
    ddb.update_item(
        TableName=TABLE,
        Key={"job_id": {"S": job_id}},
        UpdateExpression="SET #s = :s, batch_job_id = :b, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": {"S": "SUBMITTED"},
            ":b": {"S": job_ref},
            ":u": {"S": _now()},
        },
    )
    return job_id, None


def _sources(event):
    """Yield (bucket, key) pairs from either an EventBridge S3 event or a raw
    S3 notification event."""
    # EventBridge "Object Created" event
    if event.get("detail-type") == "Object Created" or "detail" in event:
        detail = event.get("detail", {})
        bucket = detail.get("bucket", {}).get("name")
        key = detail.get("object", {}).get("key")
        if bucket and key:
            yield bucket, key
        return
    # Raw S3 notification event
    for record in event.get("Records", []):
        yield record["s3"]["bucket"]["name"], record["s3"]["object"]["key"]


def handler(event, context):
    submitted, errors = [], []
    for bucket, key in _sources(event):
        if not key.lower().endswith(".csv"):
            continue  # only process CSV drops
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
            if head["ContentLength"] > MAX_CSV_SIZE:
                print(f"CSV too large: {head['ContentLength']} bytes, max {MAX_CSV_SIZE}")
                errors.append({"key": key, "error": f"CSV exceeds {MAX_CSV_SIZE} byte limit"})
                continue
            obj = s3.get_object(Bucket=bucket, Key=key)
            text = obj["Body"].read().decode("utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[csv_dispatch] could not read s3://{bucket}/{key}: {e!r}", flush=True)
            errors.append({"key": key, "error": "could not read object"})
            continue

        reader = csv.DictReader(io.StringIO(text))
        for i, row in enumerate(reader, 1):
            params, err = validate_row(row)
            if err:
                errors.append({"key": key, "row": i, "error": err})
                continue
            try:
                job_id, err = stage_and_submit(params)
            except Exception as e:  # noqa: BLE001
                print(f"[csv_dispatch] row {i} in {key} failed: {e!r}", flush=True)
                errors.append({"key": key, "row": i, "error": "submission failed"})
                continue
            if err:
                errors.append({"key": key, "row": i, "error": err})
            else:
                submitted.append(job_id)

    print(json.dumps({"submitted": len(submitted), "errors": errors}))
    return {"submitted": submitted, "errors": errors}
