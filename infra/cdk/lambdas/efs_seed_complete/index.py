"""Is-complete handler for the EFS seeder custom resource.

Runs under the CDK custom-resources Provider framework as the ``is_complete``
poller. The framework calls it repeatedly (on the Provider's query interval)
until it returns ``{"IsComplete": True}`` or the total timeout elapses.

It reads the SSM status parameter that the seeder EC2 writes:
  - "complete"        -> the download finished; the resource is complete.
  - "failed: <why>"   -> raise, so the custom resource (and the deploy) fails
                         visibly instead of leaving EFS silently empty.
  - anything else / not-yet-set ("in-progress") -> not complete; keep polling.

On Update/Delete there is nothing to wait for, so it reports complete
immediately (the on-event handler is a no-op for those).

Environment:
  STATUS_PARAM  SSM parameter name for the seed status signal
"""
import os

import boto3

ssm = boto3.client("ssm")

STATUS_PARAM = os.environ["STATUS_PARAM"]


def handler(event, context):
    request_type = event.get("RequestType")
    if request_type != "Create":
        return {"IsComplete": True}

    try:
        value = ssm.get_parameter(Name=STATUS_PARAM)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        # Seeder hasn't reported yet.
        return {"IsComplete": False}

    if value == "complete":
        return {"IsComplete": True}
    if value.startswith("failed"):
        raise RuntimeError(f"EFS seeding failed: {value}")
    # "in-progress" or any transient value: keep polling.
    return {"IsComplete": False}
