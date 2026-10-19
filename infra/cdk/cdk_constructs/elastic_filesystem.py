from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_efs as efs
from cdk_nag import NagSuppressions
from constructs import Construct

from cdk_constructs._common import REMOVAL_POLICY_MAP

if TYPE_CHECKING:
    import aws_cdk.aws_kms as kms

    from deployment_config import DeploymentConfig


class ElasticFileSystem(Construct):
    """Encrypted EFS file system with VPC mount targets and an access point."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        vpc: ec2.IVpc,
        encryption_key: kms.IKey,
        subnet_type: ec2.SubnetType,
    ) -> None:
        super().__init__(scope, id)

        self._security_group = ec2.SecurityGroup(
            self,
            "EfsSg",
            vpc=vpc,
            description="EFS mount targets for the Aurora model store",
            allow_all_outbound=True,
        )

        self._file_system = efs.FileSystem(
            self,
            "ModelFs",
            vpc=vpc,
            encrypted=True,
            kms_key=encryption_key,
            security_group=self._security_group,
            vpc_subnets=ec2.SubnetSelection(subnet_type=subnet_type),
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            throughput_mode=efs.ThroughputMode.ELASTIC,
            removal_policy=REMOVAL_POLICY_MAP[config.removal_policy],
        )

        self._access_point = self._file_system.add_access_point(
            "ModelsAp",
            path=config.efs_models_path,
            create_acl=efs.Acl(owner_uid="1000", owner_gid="1000", permissions="0755"),
            posix_user=efs.PosixUser(uid="1000", gid="1000"),
        )

        # NFS from within the VPC (keeps the SG self-contained, no cross-stack ref).
        self._security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(2049),
            description="NFS from within the VPC",
        )
        self._security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(2999),
            description="EFS transit encryption tunnel",
        )

        # CDK Nag cannot resolve Fn::GetAtt intrinsics for VPC CIDR in the
        # security group ingress rule. The SG is scoped to VPC-internal CIDR
        # only (not 0.0.0.0/0), so this is a false positive.
        NagSuppressions.add_resource_suppressions(
            self._security_group,
            [
                {
                    "id": "CdkNagValidationFailure",
                    "reason": (
                        "CDK Nag cannot resolve Fn::GetAtt for VPC CIDR in "
                        "security group ingress rule. The SG allows NFS (2049) "
                        "from VPC CIDR only, not 0.0.0.0/0."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def file_system(self) -> efs.IFileSystem:
        return self._file_system

    @property
    def access_point(self) -> efs.IAccessPoint:
        return self._access_point
