from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_CDK_DIR = Path(__file__).resolve().parent

load_dotenv(_CDK_DIR / ".env")


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class DeploymentConfig:
    project: str
    env: str
    account: str
    region: str
    stack_prefix: str
    removal_policy: str
    encryption: str
    versioning: bool
    block_public_access: bool
    enforce_ssl: bool
    # Networking / compute settings for the Aurora pipeline stacks.
    max_azs: int = 2
    nat_gateways: int = 0
    efs_models_path: str = "/models"
    aurora_model_download_what: str = "all"
    # Launch the EFS seeder automatically on data-stack create (via a custom
    # resource). The heavy download still runs on the temporary seeder EC2; the
    # Lambda just triggers it. Set False to seed manually with scripts/seed_efs.sh.
    auto_seed_efs: bool = True
    # AWS Batch (inference) settings.
    gpu_instance_types: list[str] = field(default_factory=lambda: ["g6e.xlarge", "g6e.2xlarge"])
    batch_min_vcpus: int = 0
    batch_max_vcpus: int = 32
    worker_image_uri: str | None = None
    worker_image_tag: str = "latest"
    # CI/CD source: the CodeCommit repo + branch the pipeline tracks. Pushing to
    # this branch triggers the pipeline (git-based, no manual source upload).
    source_repo: str = "aurora-cicd"
    source_branch: str = "main"
    # Bedrock model ids a request may select for the planner step. A request
    # passes `model` = one of these ids (the worker refines the brief with that
    # model via Bedrock), or "none" / omitted for editor-only (prompt verbatim).
    # "qwen" selects Aurora's native Qwen3-VL agent (runs locally on the GPU
    # worker, no Bedrock); other entries are Bedrock model ids the worker
    # refines with. "none" (editor-only) is always allowed implicitly.
    allowed_models: list[str] = field(
        default_factory=lambda: [
            "qwen",
            "us.amazon.nova-pro-v1:0",
            "us.amazon.nova-lite-v1:0",
        ]
    )
    # Sentinel model values that are NOT Bedrock ids (no bedrock:InvokeModel).
    non_bedrock_models: tuple[str, ...] = ("none", "qwen")
    # Ingestion request validation bounds (mirrored in the validate Lambda).
    max_num_frames: int = 161
    min_num_frames: int = 5
    max_dimension: int = 1280
    allowed_video_ext: list[str] = field(default_factory=lambda: [".mp4", ".mov", ".webm"])
    allowed_image_ext: list[str] = field(default_factory=lambda: [".jpg", ".jpeg", ".png", ".webp"])
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def retain_data(self) -> bool:
        """True when data resources should survive stack deletion.

        Derived from removal_policy so it stays consistent with the S3/EFS
        removal policies (retain in prod, destroy in dev).
        """
        return self.removal_policy.lower() == "retain"

    @classmethod
    def load(cls, env: str) -> DeploymentConfig:
        config_dir = _CDK_DIR / "config"
        shared = _load_yaml(config_dir / "shared.yaml")
        env_cfg = _load_yaml(config_dir / f"{env}.yaml")
        merged = {**shared, **env_cfg}

        account = os.environ.get("CDK_DEPLOY_ACCOUNT", "")
        region = os.environ.get("CDK_DEPLOY_REGION", "")

        if not account:
            raise ValueError(
                "CDK_DEPLOY_ACCOUNT is not set. "
                "Set it in infra/cdk/.env or as an environment variable."
            )
        if not region:
            raise ValueError(
                "CDK_DEPLOY_REGION is not set. "
                "Set it in infra/cdk/.env or as an environment variable."
            )

        return cls(
            project=merged["project"],
            env=merged["env"],
            account=account,
            region=region,
            stack_prefix=merged["stack_prefix"],
            removal_policy=merged["removal_policy"],
            encryption=merged.get("encryption", "s3_managed"),
            versioning=merged.get("versioning", True),
            block_public_access=merged.get("block_public_access", True),
            enforce_ssl=merged.get("enforce_ssl", True),
            max_azs=merged.get("max_azs", 2),
            nat_gateways=merged.get("nat_gateways", 0),
            efs_models_path=merged.get("efs_models_path", "/models"),
            aurora_model_download_what=merged.get("aurora_model_download_what", "all"),
            auto_seed_efs=merged.get("auto_seed_efs", True),
            gpu_instance_types=merged.get("gpu_instance_types", ["g6e.xlarge", "g6e.2xlarge"]),
            batch_min_vcpus=merged.get("batch_min_vcpus", 0),
            batch_max_vcpus=merged.get("batch_max_vcpus", 32),
            worker_image_uri=merged.get("worker_image_uri"),
            worker_image_tag=merged.get("worker_image_tag", "latest"),
            source_repo=merged.get("source_repo", "aurora-cicd"),
            source_branch=merged.get("source_branch", "main"),
            allowed_models=merged.get(
                "allowed_models",
                ["qwen", "us.amazon.nova-pro-v1:0", "us.amazon.nova-lite-v1:0"],
            ),
            max_num_frames=merged.get("max_num_frames", 161),
            min_num_frames=merged.get("min_num_frames", 5),
            max_dimension=merged.get("max_dimension", 1280),
            allowed_video_ext=merged.get("allowed_video_ext", [".mp4", ".mov", ".webm"]),
            allowed_image_ext=merged.get("allowed_image_ext", [".jpg", ".jpeg", ".png", ".webp"]),
            tags=merged.get("tags", {}),
        )

    def resource_name(self, component: str, resource_type: str) -> str:
        return f"{self.stack_prefix}-{component}-{resource_type}"

    def bucket_name(self, component: str) -> str:
        """Globally-unique S3 bucket name.

        S3 bucket names share a single global namespace across all AWS accounts,
        so they must be unique worldwide. Append account id + region to the
        prefix to guarantee uniqueness while staying deterministic (so tooling
        like upload_source.sh can compute the same name).
        """
        return f"{self.stack_prefix}-{component}-{self.account}-{self.region}"

    def stack_name(self, name: str) -> str:
        return f"{self.stack_prefix}-{name}"
