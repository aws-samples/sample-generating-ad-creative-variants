from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.aws_codebuild as codebuild
import cdk_nag
from constructs import Construct

from cdk_constructs.secure_bucket import SecureBucket

if TYPE_CHECKING:
    import aws_cdk.aws_ecr as ecr
    import aws_cdk.aws_kms as kms
    import aws_cdk.aws_s3 as s3

    from deployment_config import DeploymentConfig


class ImageBuild(Construct):
    """On-demand CodeBuild project that builds and pushes a container image.

    The image is large (CUDA + torch + flash-attn) and targets linux/amd64,
    so building it in CodeBuild (amd64, managed) avoids slow local cross-builds.
    It is not part of the deploy pipeline — you start it manually (or via
    `make build-worker-cloud`) when the worker code/deps change.

    Source is an S3 zip of the minimal build context (aurora-overlay/ +
    infra/docker/), uploaded by the make target.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        repository: ecr.IRepository,
        encryption_key: kms.IKey | None = None,
        source_key: str = "worker-src.zip",
    ) -> None:
        super().__init__(scope, id)

        # Bucket the build context zip is uploaded to (source for CodeBuild).
        source_construct = SecureBucket(self, "SourceBucket", config=config, component="worker-src")
        self._source_bucket: s3.IBucket = source_construct.bucket
        cdk_nag.NagSuppressions.add_resource_suppressions(
            source_construct.bucket,
            suppressions=[
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-S1",
                    reason=(
                        "CI/CD build-context bucket. Contains only "
                        "ephemeral zip archives of source code uploaded "
                        "for CodeBuild. Not customer data."
                    ),
                ),
            ],
        )

        self._project = codebuild.Project(
            self,
            "Build",
            project_name=config.resource_name("worker-image", "build"),
            source=codebuild.Source.s3(bucket=self._source_bucket, path=source_key),
            encryption_key=encryption_key,
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                compute_type=codebuild.ComputeType.LARGE,
                privileged=True,  # required for `docker build`
            ),
            environment_variables={
                "REPO_URI": codebuild.BuildEnvironmentVariable(value=repository.repository_uri),
                "IMAGE_TAG": codebuild.BuildEnvironmentVariable(value=config.worker_image_tag),
                "REGION": codebuild.BuildEnvironmentVariable(value=cdk.Stack.of(self).region),
                "ACCOUNT": codebuild.BuildEnvironmentVariable(value=cdk.Stack.of(self).account),
            },
            timeout=cdk.Duration.hours(2),
            build_spec=codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "phases": {
                        "pre_build": {
                            "commands": [
                                "aws ecr get-login-password --region $REGION | "
                                "docker login --username AWS --password-stdin "
                                "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com",
                            ]
                        },
                        "build": {
                            "commands": [
                                "docker build -f infra/docker/worker/Dockerfile "
                                "-t $REPO_URI:$IMAGE_TAG .",
                            ]
                        },
                        "post_build": {"commands": ["docker push $REPO_URI:$IMAGE_TAG"]},
                    },
                }
            ),
        )

        # Allow the build to push the image to ECR.
        repository.grant_pull_push(self._project)

    @property
    def source_bucket(self) -> s3.IBucket:
        return self._source_bucket

    @property
    def project_name(self) -> str:
        return self._project.project_name
