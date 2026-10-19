#!/usr/bin/env bash
# One-shot EFS seeding: launch the temporary seeder instance into a PUBLIC
# subnet so it can download model weights from Hugging Face (via the Internet
# Gateway, no NAT) and write them onto the EFS model store, then self-terminate.
#
# The instance runs the userdata baked into the seeder launch template
# (see infra/cdk/cdk_constructs/efs_seeder.py): mount EFS -> download weights
# -> shutdown (terminate).
#
# Usage:
#   ./seed_efs.sh [--profile NAME] [--region REGION] [--env ENV]
#
# Watch progress via SSM Session Manager on the launched instance
# (tail -f /var/log/efs-seed.log). The instance terminates when done.
set -euo pipefail

ENV="dev"
PROFILE=""
REGION="us-east-1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="--profile $2"; shift 2;;
    --region)  REGION="$2"; shift 2;;
    --env)     ENV="$2"; shift 2;;
    *) echo "unknown arg: $1"; exit 1;;
  esac
done

# Launch template name is deterministic: {stack_prefix}-efs-seeder-lt.
LT_NAME="aurora-$ENV-efs-seeder-lt"

aws() { command aws $PROFILE --region "$REGION" "$@"; }

echo ">> Resolving seeder launch template ($LT_NAME) + a public subnet"
LT_ID=$(aws ec2 describe-launch-templates --launch-template-names "$LT_NAME" \
  --query "LaunchTemplates[0].LaunchTemplateId" --output text 2>/dev/null || true)
[[ -z "$LT_ID" || "$LT_ID" == "None" ]] && { echo "ERROR: launch template '$LT_NAME' not found (is the data stack deployed?)"; exit 1; }

# Find the VPC that owns the launch template's security group, then a public
# subnet in it (CDK tags public subnets with aws-cdk:subnet-type=Public).
VPC_ID=$(aws ec2 describe-launch-template-versions --launch-template-id "$LT_ID" \
  --versions '$Latest' \
  --query "LaunchTemplateVersions[0].LaunchTemplateData.SecurityGroupIds[0]" --output text \
  | xargs -I{} aws ec2 describe-security-groups --group-ids {} \
      --query "SecurityGroups[0].VpcId" --output text)

PUBLIC_SUBNET=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" \
            "Name=tag:aws-cdk:subnet-type,Values=Public" \
  --query "Subnets[0].SubnetId" --output text)
[[ -z "$PUBLIC_SUBNET" || "$PUBLIC_SUBNET" == "None" ]] && { echo "ERROR: no public subnet found in $VPC_ID"; exit 1; }

echo "    launch_template=$LT_ID vpc=$VPC_ID public_subnet=$PUBLIC_SUBNET"

echo ">> Launching seeder instance (public IP, self-terminates when done)"
INSTANCE_ID=$(aws ec2 run-instances \
  --launch-template "LaunchTemplateId=$LT_ID,Version=\$Latest" \
  --subnet-id "$PUBLIC_SUBNET" \
  --associate-public-ip-address \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=aurora-efs-seeder}]' \
  --query "Instances[0].InstanceId" --output text)

echo ">> Seeder launched: $INSTANCE_ID"
echo "   Follow progress:  aws ssm start-session --target $INSTANCE_ID $PROFILE --region $REGION"
echo "   Then:             sudo tail -f /var/log/efs-seed.log"
echo "   The instance terminates automatically after the weights are on EFS."
