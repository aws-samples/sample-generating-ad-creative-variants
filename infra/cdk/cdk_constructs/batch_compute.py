from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.aws_batch as batch
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecr as ecr
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
from constructs import Construct

if TYPE_CHECKING:
    import aws_cdk.aws_efs as efs

    from deployment_config import DeploymentConfig


class BatchCompute(Construct):
    """AWS Batch compute environment, job queue, and container job definition.

    Owns the Batch job IAM role. Consumers pass managed policies for S3,
    DynamoDB, KMS, and Bedrock access (no inline policies).
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
        managed_policies: list[iam.IManagedPolicy],
        subnet_type: ec2.SubnetType,
    ) -> None:
        super().__init__(scope, id)

        # ── Job role: what the running container is allowed to do ────────────
        self._job_role = iam.Role(
            self,
            "BatchJobRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Aurora Batch worker: read staging, write results, update jobs",
        )

        # Attach all managed policies (S3, DynamoDB, KMS, Bedrock)
        for policy in managed_policies:
            self._job_role.add_managed_policy(policy)

        self._repository: ecr.Repository | None = None

        # ── Compute environment (managed EC2, GPU instances) ─────────────────
        self._compute_env = batch.ManagedEc2EcsComputeEnvironment(
            self,
            "GpuCompute",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=subnet_type),
            instance_types=[ec2.InstanceType(t) for t in config.gpu_instance_types],
            minv_cpus=config.batch_min_vcpus,
            maxv_cpus=config.batch_max_vcpus,
            use_optimal_instance_classes=False,  # GPU types only
            spot=False,
        )

        # NFS to the EFS model store is granted at the filesystem level (NFS from
        # the VPC CIDR, in ElasticFileSystem) to keep the EFS security group
        # self-contained in the data stack and avoid a cross-stack dep cycle.

        # ── Job queue ────────────────────────────────────────────────────────
        self._job_queue = batch.JobQueue(self, "JobQueue", priority=1)
        self._job_queue.add_compute_environment(self._compute_env, order=1)

        # ── Container image (prebaked ECR image, or a fresh repo to push to) ──
        container_image: ecs.ContainerImage
        if config.worker_image_uri:
            container_image = ecs.ContainerImage.from_registry(config.worker_image_uri)
        else:
            self._repository = ecr.Repository(
                self,
                "WorkerRepo",
                repository_name=config.resource_name("worker", "repo"),
                image_scan_on_push=True,
                removal_policy=(
                    cdk.RemovalPolicy.RETAIN if config.retain_data else cdk.RemovalPolicy.DESTROY
                ),
                empty_on_delete=not config.retain_data,
            )
            container_image = ecs.ContainerImage.from_ecr_repository(
                self._repository, tag=config.worker_image_tag
            )

        # ── EFS volume for the model store (read-only) ───────────────────────
        models_volume = batch.EfsVolume(
            name="models",
            file_system=file_system,
            container_path=config.efs_models_path,
            access_point_id=access_point.access_point_id,
            use_job_role=True,
            enable_transit_encryption=True,
            readonly=True,
        )

        # ── Job definition ───────────────────────────────────────────────────
        self._job_definition = batch.EcsJobDefinition(
            self,
            "EditJobDef",
            container=batch.EcsEc2ContainerDefinition(
                self,
                "EditContainer",
                image=container_image,
                cpu=4,
                memory=cdk.Size.gibibytes(28),
                gpu=1,
                job_role=self._job_role,
                volumes=[models_volume],
                environment={
                    "MODELS_DIR": config.efs_models_path,
                    "AURORA_DIR": "/opt/aurora/Aurora",
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                },
            ),
            retry_attempts=2,
        )

    @property
    def repository(self) -> ecr.Repository | None:
        """The ECR repo created for the worker image (None if an external image
        URI was supplied)."""
        return self._repository

    @property
    def job_queue(self) -> batch.JobQueue:
        return self._job_queue

    @property
    def job_queue_arn(self) -> str:
        return self._job_queue.job_queue_arn

    @property
    def job_definition_arn(self) -> str:
        return self._job_definition.job_definition_arn
