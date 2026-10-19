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


def test_no_nat_gateway(dev_config: DeploymentConfig) -> None:
    _template(dev_config).resource_count_is("AWS::EC2::NatGateway", 0)


def test_no_eip(dev_config: DeploymentConfig) -> None:
    _template(dev_config).resource_count_is("AWS::EC2::EIP", 0)


def test_gateway_endpoints(dev_config: DeploymentConfig) -> None:
    template = _template(dev_config)
    gateway = template.find_resources(
        "AWS::EC2::VPCEndpoint", {"Properties": {"VpcEndpointType": "Gateway"}}
    )
    assert len(gateway) == 2


def test_interface_endpoints(dev_config: DeploymentConfig) -> None:
    template = _template(dev_config)
    interface = template.find_resources(
        "AWS::EC2::VPCEndpoint", {"Properties": {"VpcEndpointType": "Interface"}}
    )
    assert len(interface) == 10


def test_kms_key_rotation_enabled(dev_config: DeploymentConfig) -> None:
    _template(dev_config).has_resource_properties("AWS::KMS::Key", {"EnableKeyRotation": True})
