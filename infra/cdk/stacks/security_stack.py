from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_iam as iam
import aws_cdk.aws_kms as kms

from cdk_constructs.kms_key import KmsKey
from cdk_constructs.vpc import Vpc

if TYPE_CHECKING:
    from constructs import Construct

    from deployment_config import DeploymentConfig


class SecurityStack(cdk.Stack):
    """Network + encryption foundation: VPC (endpoints, no NAT) and a KMS key."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        network = Vpc(self, "Network", config=config)
        self._vpc = network.vpc
        self._workload_subnet_type = network.workload_subnet_type

        self._kms = KmsKey(self, "DataKey", config=config)

        for key, value in config.tags.items():
            cdk.Tags.of(self).add(key, value)

    @property
    def vpc(self) -> ec2.IVpc:
        return self._vpc

    @property
    def kms_key(self) -> kms.IKey:
        return self._kms.key

    @property
    def kms_encrypt_decrypt_policy(self) -> iam.ManagedPolicy:
        return self._kms.encrypt_decrypt_policy

    @property
    def workload_subnet_type(self) -> ec2.SubnetType:
        return self._workload_subnet_type
