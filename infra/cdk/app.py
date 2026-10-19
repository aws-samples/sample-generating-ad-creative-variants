#!/usr/bin/env python3
from __future__ import annotations

import aws_cdk as cdk
import cdk_nag

from deployment_config import DeploymentConfig
from stacks.pipeline_stack import PipelineStack

app = cdk.App()

env_name = app.node.try_get_context("env") or "dev"
config = DeploymentConfig.load(env_name)

stack = PipelineStack(
    app,
    config.stack_name("pipeline"),
    config=config,
    env=cdk.Environment(account=config.account, region=config.region),
)

cdk.Tags.of(app).add("aurora:project", config.project)
cdk.Tags.of(app).add("aurora:env", config.env)
cdk.Tags.of(app).add("aurora:managed-by", "cdk-pipeline")
cdk.Tags.of(app).add("aurora:stack-prefix", config.stack_prefix)

cdk.Aspects.of(app).add(cdk_nag.AwsSolutionsChecks(verbose=True))

app.synth()
