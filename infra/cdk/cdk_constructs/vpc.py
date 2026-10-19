from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk.aws_ec2 as ec2
from cdk_nag import NagSuppressions
from constructs import Construct

if TYPE_CHECKING:
    from deployment_config import DeploymentConfig


class Vpc(Construct):
    """VPC with isolated workload subnets and VPC endpoints (no NAT by default).

    When ``config.nat_gateways`` is 0 the private subnets are isolated and reach
    AWS services through gateway (S3, DynamoDB) and interface (ECR, Logs, STS)
    endpoints. Set ``nat_gateways > 0`` to use PRIVATE_WITH_EGRESS via NAT.
    See infra/cdk/docs/adr/0001-defer-nat-gateway.md.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
    ) -> None:
        super().__init__(scope, id)

        use_nat = config.nat_gateways > 0
        private_type = (
            ec2.SubnetType.PRIVATE_WITH_EGRESS if use_nat else ec2.SubnetType.PRIVATE_ISOLATED
        )
        self._workload_subnet_type = private_type

        self._vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=config.max_azs,
            nat_gateways=config.nat_gateways,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="private",
                    subnet_type=private_type,
                    cidr_mask=22,
                ),
            ],
        )

        # VPC Flow Logs to CloudWatch (fixes AwsSolutions-VPC7).
        self._vpc.add_flow_log(
            "FlowLog",
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(),
            traffic_type=ec2.FlowLogTrafficType.ALL,
        )

        self._vpc.add_gateway_endpoint("S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3)
        self._vpc.add_gateway_endpoint(
            "DynamoEndpoint", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB
        )

        if not use_nat:
            interface_services: dict[str, ec2.InterfaceVpcEndpointAwsService] = {
                "EcrApi": ec2.InterfaceVpcEndpointAwsService.ECR,
                "EcrDkr": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
                "Logs": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
                "Sts": ec2.InterfaceVpcEndpointAwsService.STS,
                "BedrockRuntime": ec2.InterfaceVpcEndpointAwsService("bedrock-runtime"),
                "Ecs": ec2.InterfaceVpcEndpointAwsService.ECS,
                "EcsAgent": ec2.InterfaceVpcEndpointAwsService.ECS_AGENT,
                "EcsTelemetry": ec2.InterfaceVpcEndpointAwsService.ECS_TELEMETRY,
                "Batch": ec2.InterfaceVpcEndpointAwsService("batch"),
                "Ssm": ec2.InterfaceVpcEndpointAwsService.SSM,
            }
            workload_subnets = ec2.SubnetSelection(subnet_type=private_type)
            for name, svc in interface_services.items():
                endpoint = self._vpc.add_interface_endpoint(
                    name, service=svc, subnets=workload_subnets, open=True
                )
                # CDK Nag cannot validate Fn::GetAtt intrinsics used for
                # VPC CIDR in security group ingress rules. The SG only
                # allows traffic from within the VPC CIDR, not 0.0.0.0/0.
                NagSuppressions.add_resource_suppressions(
                    endpoint,
                    [
                        {
                            "id": "CdkNagValidationFailure",
                            "reason": (
                                "CDK Nag cannot resolve Fn::GetAtt for VPC CIDR "
                                "in security group rules. Ingress is VPC-internal "
                                "only (VPC CIDR), not open to the internet."
                            ),
                        },
                    ],
                    apply_to_children=True,
                )

    @property
    def vpc(self) -> ec2.IVpc:
        return self._vpc

    @property
    def workload_subnet_type(self) -> ec2.SubnetType:
        return self._workload_subnet_type
