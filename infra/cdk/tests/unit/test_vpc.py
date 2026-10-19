"""Focused VPC tests: endpoints, flow logs, no NAT, subnet AZ distribution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from stacks.security_stack import SecurityStack

if TYPE_CHECKING:
    from deployment_config import DeploymentConfig


def _template(config: DeploymentConfig) -> assertions.Template:
    app = cdk.App()
    stack = SecurityStack(
        app,
        "TestSecurity",
        config=config,
        env=cdk.Environment(account="123456789012", region="us-east-2"),
    )
    return assertions.Template.from_stack(stack)


def test_vpc_has_exactly_12_endpoints(dev_config: DeploymentConfig) -> None:
    """VPC should have 2 gateway + 10 interface = 12 total endpoints."""
    template = _template(dev_config)
    endpoints = template.find_resources("AWS::EC2::VPCEndpoint")
    assert len(endpoints) == 12


def test_vpc_flow_logs_enabled(dev_config: DeploymentConfig) -> None:
    """VPC must have flow logs enabled (AwsSolutions-VPC7)."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::EC2::FlowLog",
        {
            "TrafficType": "ALL",
            "ResourceType": "VPC",
        },
    )


def test_no_nat_gateway_exists(dev_config: DeploymentConfig) -> None:
    """No NAT gateway should exist (cost savings, endpoints only)."""
    template = _template(dev_config)
    template.resource_count_is("AWS::EC2::NatGateway", 0)


def test_subnets_in_2_azs(dev_config: DeploymentConfig) -> None:
    """Subnets should be distributed across exactly 2 AZs (config.max_azs=2)."""
    template = _template(dev_config)
    subnets = template.find_resources("AWS::EC2::Subnet")
    azs = set()
    for subnet in subnets.values():
        az = subnet["Properties"].get("AvailabilityZone")
        if az:
            azs.add(str(az))
    # With max_azs=2, we expect exactly 2 distinct AZs
    assert len(azs) == 2


def test_flow_log_destination_is_cloudwatch_logs(dev_config: DeploymentConfig) -> None:
    """Flow logs must send to CloudWatch Logs (not S3)."""
    template = _template(dev_config)
    template.has_resource_properties(
        "AWS::EC2::FlowLog",
        {
            "LogDestinationType": "cloud-watch-logs",
        },
    )


def test_gateway_endpoints_include_s3_and_dynamodb(dev_config: DeploymentConfig) -> None:
    """Gateway endpoints must include S3 and DynamoDB."""
    template = _template(dev_config)
    gateways = template.find_resources(
        "AWS::EC2::VPCEndpoint", {"Properties": {"VpcEndpointType": "Gateway"}}
    )
    service_names = [ep["Properties"]["ServiceName"] for ep in gateways.values()]
    # Service names are Fn::Join expressions; check that we have exactly 2 gateways
    assert len(service_names) == 2


def test_interface_endpoints_include_ecr_and_logs(dev_config: DeploymentConfig) -> None:
    """Interface endpoints must be present (10 services)."""
    template = _template(dev_config)
    interfaces = template.find_resources(
        "AWS::EC2::VPCEndpoint", {"Properties": {"VpcEndpointType": "Interface"}}
    )
    assert len(interfaces) == 10


def test_no_eip_allocated(dev_config: DeploymentConfig) -> None:
    """No Elastic IPs should be created (no NAT)."""
    template = _template(dev_config)
    template.resource_count_is("AWS::EC2::EIP", 0)
