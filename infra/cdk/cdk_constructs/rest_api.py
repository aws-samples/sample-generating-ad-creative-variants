from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk.aws_apigateway as apigw
import aws_cdk.aws_logs as logs
from cdk_nag import NagSuppressions
from constructs import Construct

if TYPE_CHECKING:
    import aws_cdk.aws_lambda as lambda_

    from deployment_config import DeploymentConfig


class RestApi(Construct):
    """REST API with IAM authorization and usage-plan throttling.

    All methods require IAM (SigV4) authorization. API keys are retained
    for rate-limiting via a usage plan but do NOT serve as the auth layer.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        handler: lambda_.IFunction,
    ) -> None:
        super().__init__(scope, id)

        # Access log group for API Gateway (fixes AwsSolutions-APIG1).
        access_log_group = logs.LogGroup(
            self,
            "AccessLogs",
            retention=logs.RetentionDays.ONE_WEEK,
        )

        self._api = apigw.RestApi(
            self,
            "Api",
            rest_api_name=config.resource_name("jobs", "api"),
            endpoint_configuration=apigw.EndpointConfiguration(types=[apigw.EndpointType.REGIONAL]),
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                throttling_rate_limit=25,
                throttling_burst_limit=10,
                # Access logging (fixes AwsSolutions-APIG1).
                access_log_destination=apigw.LogGroupLogDestination(access_log_group),
                access_log_format=apigw.AccessLogFormat.clf(),
                # Execution logging (fixes AwsSolutions-APIG6).
                logging_level=apigw.MethodLoggingLevel.INFO,
            ),
        )

        # ── Request validator (fixes AwsSolutions-APIG2) ────────────────────
        validator = self._api.add_request_validator(
            "RequestValidator",
            validate_request_body=True,
        )

        # JSON model for POST /jobs request body.
        job_request_model = self._api.add_model(
            "JobRequestModel",
            content_type="application/json",
            model_name="JobRequest",
            schema=apigw.JsonSchema(
                type=apigw.JsonSchemaType.OBJECT,
                required=["video_name", "prompt"],
                properties={
                    "video_name": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                    "prompt": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                    "model": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                    "num_frames": apigw.JsonSchema(type=apigw.JsonSchemaType.INTEGER),
                },
            ),
        )

        # ── Resources and methods ──────────────────────────────────────────
        jobs = self._api.root.add_resource("jobs")
        integration = apigw.LambdaIntegration(handler, proxy=True)

        jobs.add_method(
            "POST",
            integration,
            api_key_required=True,
            authorization_type=apigw.AuthorizationType.IAM,
            request_validator=validator,
            request_models={"application/json": job_request_model},
        )

        job_item = jobs.add_resource("{job_id}")
        job_item.add_method(
            "GET",
            integration,
            api_key_required=True,
            authorization_type=apigw.AuthorizationType.IAM,
        )

        # ── API key + usage plan (throttling only, not auth) ────────────────
        self._api_key = self._api.add_api_key(
            "ApiKey", api_key_name=config.resource_name("jobs", "apikey")
        )
        plan = self._api.add_usage_plan(
            "UsagePlan",
            name=config.resource_name("jobs", "plan"),
            throttle=apigw.ThrottleSettings(rate_limit=25, burst_limit=10),
        )
        plan.add_api_key(self._api_key)
        plan.add_api_stage(stage=self._api.deployment_stage)

        # ── CDK Nag suppressions ────────────────────────────────────────────
        # WAF is not required for this internal dev API secured by IAM auth.
        # APIG2: request validator is enabled above, but CDK Nag flags
        # sub-resources that inherit from the API.
        # COG4: IAM (SigV4) authorization is used; Cognito is not required.
        NagSuppressions.add_resource_suppressions(
            self._api,
            [
                {
                    "id": "AwsSolutions-APIG3",
                    "reason": "WAF not required for internal dev API with IAM auth",
                },
                {
                    "id": "AwsSolutions-APIG2",
                    "reason": (
                        "Request validation is enabled on POST /jobs. GET methods"
                        " have no body to validate. CDK Nag flags the API-level"
                        " default which does not override method-level validators."
                    ),
                },
                {
                    "id": "AwsSolutions-COG4",
                    "reason": (
                        "IAM (SigV4) authorization is used instead of Cognito."
                        " API keys provide throttling, not auth."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def url(self) -> str:
        return self._api.url

    @property
    def api_key_id(self) -> str:
        return self._api_key.key_id
