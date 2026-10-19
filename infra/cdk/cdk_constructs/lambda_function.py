from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.aws_lambda as lambda_
import aws_cdk.aws_logs as logs
from constructs import Construct

if TYPE_CHECKING:
    import aws_cdk.aws_ec2 as ec2


class LambdaFunction(Construct):
    """Lambda function from a local asset directory.

    Defaults: 1-week log retention, active tracing, optional VPC placement and
    EFS mount. Handler code lives under ``lambdas/`` (excluded from the strict
    lint/type gates as a deployed asset).
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        code_path: str,
        handler: str = "index.handler",
        timeout_seconds: int = 60,
        memory_mb: int = 256,
        environment: dict[str, str] | None = None,
        vpc: ec2.IVpc | None = None,
        vpc_subnets: ec2.SubnetSelection | None = None,
        filesystem: lambda_.FileSystem | None = None,
    ) -> None:
        super().__init__(scope, id)

        log_group = logs.LogGroup(
            self,
            "LogGroup",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        self._function = lambda_.Function(
            self,
            "Fn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler=handler,
            code=lambda_.Code.from_asset(code_path),
            timeout=cdk.Duration.seconds(timeout_seconds),
            memory_size=memory_mb,
            environment=environment or {},
            tracing=lambda_.Tracing.ACTIVE,
            log_group=log_group,
            vpc=vpc,
            vpc_subnets=vpc_subnets,
            filesystem=filesystem,
        )

    @property
    def function(self) -> lambda_.Function:
        return self._function
