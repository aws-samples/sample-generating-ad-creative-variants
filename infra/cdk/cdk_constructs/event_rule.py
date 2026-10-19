from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk.aws_events as events
import aws_cdk.aws_events_targets as targets
from constructs import Construct

if TYPE_CHECKING:
    import aws_cdk.aws_lambda as lambda_


class EventRule(Construct):
    """EventBridge rule that triggers a Lambda on matching events."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        bucket_name: str,
        prefix: str,
        target: lambda_.IFunction,
    ) -> None:
        super().__init__(scope, id)

        self._rule = events.Rule(
            self,
            "Rule",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [bucket_name]},
                    "object": {"key": [{"prefix": prefix}]},
                },
            ),
        )
        self._rule.add_target(targets.LambdaFunction(target))

    @property
    def rule(self) -> events.Rule:
        return self._rule
