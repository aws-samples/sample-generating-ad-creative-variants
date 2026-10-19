from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.aws_codebuild as codebuild
import aws_cdk.aws_codecommit as codecommit
import aws_cdk.aws_codepipeline as codepipeline
import aws_cdk.aws_iam as iam
import aws_cdk.pipelines as pipelines
import cdk_nag
from constructs import Construct

from cdk_constructs.build_step import BuildStep
from cdk_constructs.secure_bucket import SecureBucket

if TYPE_CHECKING:
    from deployment_config import DeploymentConfig

# The CDK app lives in this subdirectory of the git repo (the pipeline source is
# the whole repo, so synth/build commands cd into it).
_CDK_DIR = "infra/cdk"


class Pipeline(Construct):
    """CodePipeline with a CodeCommit git source, synth step, and configurable
    quality/security gates. Pushing to the tracked branch triggers the pipeline."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        deploy_stage: cdk.Stage,
    ) -> None:
        super().__init__(scope, id)

        artifacts = SecureBucket(
            self,
            "ArtifactsBucket",
            config=config,
            component="cicd-artifacts",
        )
        cdk_nag.NagSuppressions.add_resource_suppressions(
            artifacts.bucket,
            suppressions=[
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-S1",
                    reason=(
                        "CI/CD artifacts bucket. Access logging is not "
                        "required for pipeline-internal artifacts that "
                        "contain only build outputs and CloudFormation "
                        "templates, not customer data."
                    ),
                ),
            ],
        )

        repository = codecommit.Repository.from_repository_name(
            self, "SourceRepo", config.source_repo
        )
        # EVENTS trigger (default): pushes to the branch auto-start the pipeline.
        source = pipelines.CodePipelineSource.code_commit(repository, config.source_branch)

        synth = pipelines.ShellStep(
            "Synth",
            input=source,
            install_commands=[
                "npm install -g aws-cdk@2.1132.1",
                # Subshell: CodeBuild persists cwd across phases, so scope the cd.
                f"(cd {_CDK_DIR} && pip install -r requirements.txt)",
            ],
            commands=[
                f"(cd {_CDK_DIR} && CDK_DEPLOY_ACCOUNT={config.account}"
                f" CDK_DEPLOY_REGION={config.region} cdk synth -c env={config.env})",
            ],
            primary_output_directory=f"{_CDK_DIR}/cdk.out",
        )

        self._pipeline = pipelines.CodePipeline(
            self,
            "Pipeline",
            pipeline_name=config.resource_name("cicd", "pipeline"),
            synth=synth,
            cross_account_keys=False,
            pipeline_type=codepipeline.PipelineType.V2,
            artifact_bucket=artifacts.bucket,
        )

        quality_gate = BuildStep.quality_gate(config)
        test_gate = BuildStep.unit_tests(config)
        security_gate = BuildStep.security_gate(config)

        self._pipeline.add_wave(
            "Gates",
            post=[
                quality_gate.step,
                test_gate.step,
                security_gate.step,
            ],
        )

        # Build + push the GPU worker image after the stacks deploy, but only
        # when we own the ECR repo (no external worker_image_uri). The step is
        # present on every pipeline run, but it hashes the worker build inputs
        # and skips the (expensive) build+push entirely when that exact content
        # is already in ECR — so the CUDA/torch/flash-attn build only runs on
        # real worker code changes.
        post_steps: list[pipelines.Step] = []
        if not config.worker_image_uri:
            post_steps.append(self._worker_image_step(config, source))

        self._pipeline.add_stage(
            deploy_stage,
            post=post_steps or None,
        )

        # Force the pipeline to materialize its internal constructs (Synth,
        # UpdatePipeline, Assets CodeBuild projects) so path-based Nag
        # suppressions can attach to them.
        self._pipeline.build_pipeline()

        # CDK Pipelines manages its own internal CodeBuild projects (Synth,
        # UpdatePipeline, Assets) and does not expose a way to pass a KMS
        # encryption key to them. Suppress AwsSolutions-CB4 at the construct
        # tree level; the artifact bucket is already KMS-encrypted via S3.
        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(
            cdk.Stack.of(self),
            path=f"{self.node.path}/Pipeline",
            suppressions=[
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-CB4",
                    reason=(
                        "CDK Pipelines internally creates CodeBuild projects "
                        "for Synth/UpdatePipeline/Assets steps. The construct "
                        "does not expose a mechanism to configure KMS "
                        "encryption on these managed projects. Pipeline "
                        "artifacts are encrypted at rest in S3 via the "
                        "artifact bucket's KMS/S3-managed encryption."
                    ),
                ),
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-CB3",
                    reason=(
                        "Privileged mode is required for the worker image "
                        "build step (docker build inside CodeBuild)."
                    ),
                ),
            ],
            apply_to_children=True,
        )

        # ── IAM5: Pipeline-generated wildcard permissions ───────────────────
        # CodeBuild projects created by CDK Pipelines (Synth, SelfMutation,
        # gates, worker image build) are granted log-group:* and
        # report-group:* wildcards by CDK. The resources are scoped to this
        # account/region and specific CodeBuild project names.
        cdk_nag.NagSuppressions.add_stack_suppressions(
            cdk.Stack.of(self),
            [
                cdk_nag.NagPackSuppression(
                    id="AwsSolutions-IAM5",
                    reason=(
                        "CDK Pipelines and CodeBuild grant log-group and "
                        "report-group ARNs with trailing wildcards scoped to "
                        "specific project names. Artifact bucket object-level "
                        "wildcards are scoped to the pipeline's own bucket ARN. "
                        "Self-mutation requires iam:PassRole with Resource::* "
                        "to assume roles created by CloudFormation."
                    ),
                ),
            ],
        )

    def _worker_image_step(
        self, config: DeploymentConfig, source: pipelines.CodePipelineSource
    ) -> pipelines.CodeBuildStep:
        """CodeBuild step that builds and pushes the worker image to ECR.

        The build context is the git repo root (the pipeline source), which
        already has the aurora-overlay/ + infra/docker/ layout the worker
        Dockerfile's COPY paths expect — so the build runs from there directly.

        The step is change-gated: it hashes the worker build inputs
        (aurora-overlay/ + infra/docker/worker/) into a content tag and only
        builds+pushes when that tag is not already in ECR. Unchanged inputs ->
        the heavy CUDA/torch/flash-attn build is skipped entirely, and the
        moving tag is just re-pointed at the existing image.
        """
        repo_name = config.resource_name("worker", "repo")
        repo_uri = f"{config.account}.dkr.ecr.{config.region}.amazonaws.com/{repo_name}"
        tag = config.worker_image_tag
        registry = f"{config.account}.dkr.ecr.{config.region}.amazonaws.com"

        # One shell command so the if/else and shell vars share a scope. Hash the
        # files that actually go into the image; identical content -> identical
        # tag -> found in ECR -> skip. On a hit we re-point the moving tag
        # (IMAGE_TAG) at the existing manifest so the Batch job def resolves it.
        gate = (
            "HASH=$(find aurora-overlay infra/docker/worker -type f "
            '! -name "*.pyc" ! -path "*/__pycache__/*" -print0 '
            "| sort -z | xargs -0 sha256sum | sha256sum | cut -c1-12); "
            'CONTENT_TAG="src-$HASH"; '
            'echo "worker content tag: $CONTENT_TAG"; '
            'if aws ecr describe-images --repository-name "$REPO_NAME" '
            '--image-ids imageTag="$CONTENT_TAG" >/dev/null 2>&1; then '
            '  echo "worker inputs unchanged; skipping build"; '
            '  MANIFEST=$(aws ecr batch-get-image --repository-name "$REPO_NAME" '
            '  --image-ids imageTag="$CONTENT_TAG" '
            '  --query "images[0].imageManifest" --output text); '
            '  aws ecr put-image --repository-name "$REPO_NAME" '
            '  --image-tag "$IMAGE_TAG" --image-manifest "$MANIFEST" '
            "  >/dev/null 2>&1 || true; "
            "else "
            '  echo "worker inputs changed; building"; '
            # Reuse the last image as a cache source only if it exists. On the
            # first build the repo is empty and BuildKit treats a missing
            # --cache-from ref as a fatal error, so gate it on a successful pull.
            '  if docker pull "$REPO_URI:$IMAGE_TAG"; then '
            '    CACHE_ARGS="--cache-from $REPO_URI:$IMAGE_TAG"; '
            '  else CACHE_ARGS=""; fi; '
            # --network=host: RUN steps use CodeBuild's network instead of the
            # docker0 bridge, whose MTU breaks large apt downloads (indexes fetch
            # but .deb transfers stall) in the CodeBuild environment.
            "  DOCKER_BUILDKIT=1 docker build --network=host "
            "  --build-arg BUILDKIT_INLINE_CACHE=1 "
            "  $CACHE_ARGS "
            "  -f infra/docker/worker/Dockerfile "
            '  -t "$REPO_URI:$IMAGE_TAG" -t "$REPO_URI:$CONTENT_TAG" .; '
            '  docker push "$REPO_URI:$IMAGE_TAG"; '
            '  docker push "$REPO_URI:$CONTENT_TAG"; '
            "fi"
        )

        return pipelines.CodeBuildStep(
            "BuildWorkerImage",
            input=source,
            build_environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                compute_type=codebuild.ComputeType.LARGE,
                privileged=True,  # required for `docker build`
            ),
            env={
                "ACCOUNT": config.account,
                "REGION": config.region,
                "REPO_NAME": repo_name,
                "REPO_URI": repo_uri,
                "IMAGE_TAG": tag,
                "REGISTRY": registry,
            },
            commands=[
                "aws ecr get-login-password --region $REGION | "
                "docker login --username AWS --password-stdin $REGISTRY",
                gate,
            ],
            role_policy_statements=[
                iam.PolicyStatement(
                    actions=["ecr:GetAuthorizationToken"],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    actions=[
                        "ecr:DescribeImages",
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                        "ecr:InitiateLayerUpload",
                        "ecr:UploadLayerPart",
                        "ecr:CompleteLayerUpload",
                        "ecr:PutImage",
                    ],
                    resources=[
                        f"arn:aws:ecr:{config.region}:{config.account}:repository/{repo_name}"
                    ],
                ),
            ],
        )

    @property
    def pipeline(self) -> pipelines.CodePipeline:
        return self._pipeline
