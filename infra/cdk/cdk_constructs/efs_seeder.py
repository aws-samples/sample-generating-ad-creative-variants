from __future__ import annotations

import os
from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_iam as iam
from aws_cdk import custom_resources as cr
from constructs import Construct

from cdk_constructs.lambda_function import LambdaFunction

if TYPE_CHECKING:
    import aws_cdk.aws_efs as efs

    from deployment_config import DeploymentConfig

_LAMBDAS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lambdas")

# Self-contained weight downloader run on the seeder EC2. It fetches the same
# HuggingFace repos + on-disk layout that evaluation.model_download targets, but
# using only huggingface_hub — it deliberately does NOT install or import Aurora
# (evaluation.pipeline_loader imports torch + diffsynth at module load, which the
# CPU-only seeder has no reason to install and which previously failed the seed).
_DOWNLOAD_PY = """\
import os
import shutil
from huggingface_hub import hf_hub_download, snapshot_download

model_dir = os.environ["AURORA_MODEL_DIR"]
what = os.environ.get("AURORA_WHAT", "all")
os.makedirs(model_dir, exist_ok=True)

# The editor only needs the DiT shards + VAE; skip the ~11 GB T5 text encoder.
WAN_ALLOW = ["diffusion_pytorch_model-*.safetensors", "Wan2.2_VAE.pth", "*.json"]


def editor():
    snapshot_download(
        "Wan-AI/Wan2.2-TI2V-5B",
        local_dir=os.path.join(model_dir, "Wan2.2-TI2V-5B"),
        allow_patterns=WAN_ALLOW,
    )
    snapshot_download("Qwen/Qwen3.5-4B", local_dir=os.path.join(model_dir, "Qwen3.5-4B"))

    # Download the editor checkpoint. hf_hub_download returns the actual file path
    # regardless of local_dir behavior (which varies across huggingface_hub versions).
    downloaded = hf_hub_download(
        "yeates/aurora-weights",
        "aurora_editor.safetensors",
        local_dir=model_dir,
    )
    print(f"[seeder] hf_hub_download returned: {downloaded}")

    # Ensure the flat path exists (run_single_sample.py expects this)
    flat_path = os.path.join(model_dir, "aurora_editor.safetensors")
    if not os.path.isfile(flat_path):
        print(f"[seeder] flat path missing, copying from {downloaded}")
        shutil.copy2(downloaded, flat_path)
    print(f"[seeder] flat path verified: {flat_path} ({os.path.getsize(flat_path)} bytes)")

    # Also create aurora_video_editor/model.safetensors (editor_bridge_video expects this)
    editor_dir = os.path.join(model_dir, "aurora_video_editor")
    os.makedirs(editor_dir, exist_ok=True)
    bridge_path = os.path.join(editor_dir, "model.safetensors")
    if not os.path.isfile(bridge_path):
        shutil.copy2(flat_path, bridge_path)
    print(f"[seeder] bridge path verified: {bridge_path} ({os.path.getsize(bridge_path)} bytes)")

    # Log top-level contents for debugging
    print(f"[seeder] {model_dir}/ -> {sorted(os.listdir(model_dir))}")


def agent():
    snapshot_download(
        "Qwen/Qwen3-VL-8B-Instruct",
        local_dir=os.path.join(model_dir, "Qwen3-VL-8B-Instruct"),
    )
    snapshot_download(
        "yeates/aurora-weights", local_dir=model_dir, allow_patterns=["aurora_agent_vlm/*"]
    )


if what in ("editor", "all"):
    editor()
if what in ("agent", "all"):
    agent()
print("[seeder] model download complete")
print(f"[seeder] final contents: {sorted(os.listdir(model_dir))}")
"""


class EfsSeeder(Construct):
    """One-shot EFS seeder: a temporary public-subnet EC2 launched from a
    launch template that mounts the EFS access point, downloads model weights
    from Hugging Face via the Internet Gateway (no NAT), then self-terminates.
    See infra/cdk/docs/adr/0002-efs-seed-no-bucket.md.

    With ``auto_seed=True`` a custom resource launches the instance on stack
    create and the deploy tracks the outcome:

    - **Idempotent download:** the instance skips the (tens-of-GB) download when
      EFS already holds the weights, detected via a ``.seed_complete`` marker at
      the mount root — so a redeploy against retained EFS won't re-download.
    - **Wait-for-completion:** an is-complete handler polls an SSM status
      parameter, so the deploy only finishes once the weights have landed
      (not fire-and-forget).
    - **Failure surfacing:** if the seeder reports a failure, the is-complete
      handler raises and the deploy fails visibly instead of leaving EFS empty.

    Without ``auto_seed`` only the launch template is created; nothing runs
    until you launch it manually (see scripts/seed_efs.sh).
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        vpc: ec2.IVpc,
        file_system: efs.IFileSystem,
        access_point: efs.IAccessPoint,
        auto_seed: bool = False,
    ) -> None:
        super().__init__(scope, id)

        # SSM parameter used as the seed status signal. The seeder EC2 writes
        # "complete" / "failed: <reason>" here; the is-complete handler polls it
        # so the deploy waits for the download and surfaces failures.
        stack = cdk.Stack.of(self)
        self._status_param = f"/{config.stack_prefix}/efs-seed/status"
        status_param_arn = (
            f"arn:aws:ssm:{stack.region}:{stack.account}:parameter{self._status_param}"
        )

        self._security_group = ec2.SecurityGroup(
            self,
            "SeederSg",
            vpc=vpc,
            description="Temporary EFS seeder (public subnet, egress only)",
            allow_all_outbound=True,
        )

        # Scoped EFS policy: only allows mount + write on the specific filesystem
        # rather than the overly-broad AWS managed AmazonElasticFileSystemClientReadWriteAccess.
        efs_policy = iam.ManagedPolicy(
            self,
            "SeederEfsPolicy",
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "elasticfilesystem:ClientMount",
                        "elasticfilesystem:ClientWrite",
                    ],
                    resources=[file_system.file_system_arn],
                )
            ],
        )

        role = iam.Role(
            self,
            "SeederRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                efs_policy,
            ],
        )
        # Let the seeder report its status to the SSM parameter.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:PutParameter"],
                resources=[status_param_arn],
            )
        )

        mount_dir = "/mnt/models"
        marker = f"{mount_dir}/.seed_complete"
        ap_id = access_point.access_point_id
        fs_id = file_system.file_system_id

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "exec > /var/log/efs-seed.log 2>&1",
            f"export AWS_DEFAULT_REGION={config.region}",
            f"SSM_PARAM='{self._status_param}'",
            'report() { aws ssm put-parameter --name "$SSM_PARAM" --type String '
            '--overwrite --value "$1" || true; }',
            "set -x",
            "dnf install -y amazon-efs-utils python3-pip || "
            "yum install -y amazon-efs-utils python3-pip",
            f"mkdir -p {mount_dir}",
            # Mount is required; if it fails, report and bail so EFS isn't left empty silently.
            f"if ! mount -t efs -o tls,accesspoint={ap_id} {fs_id}: {mount_dir}; then "
            "report 'failed: efs mount'; shutdown -h now; exit 1; fi",
            # Idempotency: skip the heavy download if EFS is already seeded (marker present).
            f"if [ -f {marker} ]; then report complete; "
            f"umount {mount_dir} || true; shutdown -h now; exit 0; fi",
            # Only huggingface_hub is needed to fetch weights — no torch / Aurora install.
            "python3 -m pip install --quiet 'huggingface_hub==0.34.0'",
            f"cat > /opt/hf_download.py <<'PYEOF'\n{_DOWNLOAD_PY}PYEOF",
            f"export AURORA_MODEL_DIR={mount_dir}",
            f"export AURORA_WHAT={config.aurora_model_download_what}",
            # Keep any HF cache on elastic EFS, not the small root volume.
            f"export HF_HOME={mount_dir}/.hf_cache",
            # Download; write the marker + report complete on success, else report the failure.
            "if python3 /opt/hf_download.py; then "
            f"touch {marker}; sync; report complete; "
            'else report "failed: model_download rc=$?"; fi',
            f"umount {mount_dir} || true",
            "shutdown -h now",
        )

        self._launch_template = ec2.LaunchTemplate(
            self,
            "SeederLt",
            launch_template_name=config.resource_name("efs-seeder", "lt"),
            instance_type=ec2.InstanceType("m5.large"),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            role=role,
            security_group=self._security_group,
            user_data=user_data,
            instance_initiated_shutdown_behavior=(ec2.InstanceInitiatedShutdownBehavior.TERMINATE),
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(30, delete_on_termination=True),
                )
            ],
        )

        # ── Optional: auto-launch the seeder on stack create (custom resource) ─
        if auto_seed:
            stack = cdk.Stack.of(self)
            public_subnet_id = vpc.select_subnets(subnet_type=ec2.SubnetType.PUBLIC).subnet_ids[0]
            lt_id = self._launch_template.launch_template_id
            assert lt_id is not None  # noqa: S101 - always set post-synth

            trigger = LambdaFunction(
                self,
                "SeedTriggerFn",
                code_path=os.path.join(_LAMBDAS_DIR, "efs_seed_trigger"),
                timeout_seconds=60,
                environment={
                    "LAUNCH_TEMPLATE_ID": lt_id,
                    "PUBLIC_SUBNET_ID": public_subnet_id,
                    "STATUS_PARAM": self._status_param,
                },
            ).function

            # Allow the trigger to launch the seeder and pass its instance role.
            trigger.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["ec2:RunInstances", "ec2:CreateTags"],
                    resources=[
                        f"arn:aws:ec2:{stack.region}:{stack.account}:*",
                        f"arn:aws:ec2:{stack.region}::image/*",
                    ],
                )
            )
            trigger.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["iam:PassRole"],
                    resources=[role.role_arn],
                )
            )
            # Reset the status to in-progress before each launch so the
            # is-complete poller never reads a stale value from a prior run.
            trigger.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["ssm:PutParameter"],
                    resources=[status_param_arn],
                )
            )

            # is-complete handler: polls the SSM status so the deploy waits for
            # the download to finish and fails visibly if the seeder reports an
            # error (turns the fire-and-forget launch into a tracked outcome).
            complete = LambdaFunction(
                self,
                "SeedCompleteFn",
                code_path=os.path.join(_LAMBDAS_DIR, "efs_seed_complete"),
                timeout_seconds=30,
                environment={"STATUS_PARAM": self._status_param},
            ).function
            complete.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[status_param_arn],
                )
            )

            provider = cr.Provider(
                self,
                "SeedProvider",
                on_event_handler=trigger,
                is_complete_handler=complete,
                query_interval=cdk.Duration.minutes(2),
                total_timeout=cdk.Duration.hours(1),
            )
            cdk.CustomResource(
                self,
                "SeedTrigger",
                service_token=provider.service_token,
            )

    @property
    def launch_template(self) -> ec2.LaunchTemplate:
        return self._launch_template
