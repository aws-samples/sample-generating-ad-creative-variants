"""Custom-resource handler that auto-launches the EFS seeder on stack create.

Runs under the CDK custom-resources Provider framework (it returns a dict; the
framework handles the CloudFormation response protocol).

On Create it launches one seeder instance from the seeder launch template into a
public subnet (which auto-assigns a public IP). The instance downloads the model
weights from Hugging Face onto EFS and self-terminates — this handler is
fire-and-forget and does NOT wait for the download to finish.

On Update/Delete it is a no-op (seed once on first provision). To re-seed, run
scripts/seed_efs.sh manually.

Before launching, it resets the SSM status parameter to "in-progress" so the
companion is-complete handler (efs_seed_complete) never reads a stale
"complete"/"failed" value from a previous run. The seeder EC2 later overwrites
this with the real outcome; whether EFS actually needs a fresh download is
decided on the instance (it skips the heavy download if the EFS marker exists).

Environment:
  LAUNCH_TEMPLATE_ID  the seeder launch template
  PUBLIC_SUBNET_ID    a public subnet (MapPublicIpOnLaunch=true)
  STATUS_PARAM        SSM parameter name for the seed status signal
"""
import os

import boto3

ec2 = boto3.client("ec2")
ssm = boto3.client("ssm")

LAUNCH_TEMPLATE_ID = os.environ["LAUNCH_TEMPLATE_ID"]
PUBLIC_SUBNET_ID = os.environ["PUBLIC_SUBNET_ID"]
STATUS_PARAM = os.environ["STATUS_PARAM"]


def handler(event, context):
    request_type = event.get("RequestType")
    physical_id = f"efs-seed-{LAUNCH_TEMPLATE_ID}"

    if request_type != "Create":
        # Seed only on first provision; updates/deletes are no-ops.
        print(f"{request_type}: no-op")
        return {"PhysicalResourceId": physical_id}

    # Reset status so the is-complete poller waits for THIS run's outcome.
    ssm.put_parameter(
        Name=STATUS_PARAM, Value="in-progress", Type="String", Overwrite=True
    )

    resp = ec2.run_instances(
        LaunchTemplate={"LaunchTemplateId": LAUNCH_TEMPLATE_ID, "Version": "$Latest"},
        SubnetId=PUBLIC_SUBNET_ID,
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": "aurora-efs-seeder"}],
            }
        ],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    print(f"launched seeder instance {instance_id} in {PUBLIC_SUBNET_ID}")
    return {"PhysicalResourceId": physical_id, "Data": {"InstanceId": instance_id}}
