from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk.aws_iam as iam
import aws_cdk.aws_kms as kms
from constructs import Construct

from cdk_constructs._common import REMOVAL_POLICY_MAP

if TYPE_CHECKING:
    from deployment_config import DeploymentConfig


class KmsKey(Construct):
    """Customer-managed KMS key with automatic rotation enabled."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        component: str = "data",
    ) -> None:
        super().__init__(scope, id)

        self._config = config
        self._component = component
        self._encrypt_decrypt_policy: iam.ManagedPolicy | None = None

        self._key = kms.Key(
            self,
            "Key",
            alias=config.resource_name(component, "key"),
            enable_key_rotation=True,
            description=f"CMK for {config.stack_prefix} data at rest",
            removal_policy=REMOVAL_POLICY_MAP[config.removal_policy],
        )

    @property
    def key(self) -> kms.IKey:
        return self._key

    @property
    def encrypt_decrypt_policy(self) -> iam.ManagedPolicy:
        """Lazily created managed policy granting encrypt/decrypt on this key."""
        if self._encrypt_decrypt_policy is None:
            self._encrypt_decrypt_policy = iam.ManagedPolicy(
                self,
                "EncryptDecryptPolicy",
                managed_policy_name=self._config.resource_name(self._component, "kms-policy"),
                statements=[
                    iam.PolicyStatement(
                        actions=[
                            "kms:Encrypt",
                            "kms:Decrypt",
                            "kms:GenerateDataKey",
                            "kms:GenerateDataKeyWithoutPlaintext",
                            "kms:DescribeKey",
                            "kms:ReEncryptFrom",
                            "kms:ReEncryptTo",
                        ],
                        resources=[self._key.key_arn],
                    ),
                ],
            )
        return self._encrypt_decrypt_policy
