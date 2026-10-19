from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aws_cdk as cdk

from cdk_constructs.pipeline import Pipeline
from stages.deploy_stage import DeployStage

if TYPE_CHECKING:
    from constructs import Construct

    from deployment_config import DeploymentConfig


class PipelineStack(cdk.Stack):
    """CI/CD pipeline stack: CodeCommit git source → quality/security gates → deploy."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        deploy_stage = DeployStage(
            self,
            "Deploy",
            config=config,
            env=cdk.Environment(account=config.account, region=config.region),
        )

        Pipeline(
            self,
            "CiCdPipeline",
            config=config,
            deploy_stage=deploy_stage,
        )

        cdk.Tags.of(self).add("StackPrefix", config.stack_prefix)
        for key, value in config.tags.items():
            cdk.Tags.of(self).add(key, value)
