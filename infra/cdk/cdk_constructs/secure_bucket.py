from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
import aws_cdk.aws_iam as iam
import aws_cdk.aws_s3 as s3
import cdk_nag
from constructs import Construct

from cdk_constructs._common import REMOVAL_POLICY_MAP

if TYPE_CHECKING:
    import aws_cdk.aws_kms as kms

    from deployment_config import DeploymentConfig

__all__ = ["SecureBucket"]

_ENCRYPTION_MAP = {
    "s3_managed": s3.BucketEncryption.S3_MANAGED,
    "kms_managed": s3.BucketEncryption.KMS_MANAGED,
    "kms": s3.BucketEncryption.KMS,
}


class SecureBucket(Construct):
    """Hardened S3 bucket with encryption, versioning, SSL, and public access blocked."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        config: DeploymentConfig,
        component: str,
        auto_delete_objects: bool | None = None,
        encryption_key: kms.IKey | None = None,
        event_bridge: bool = False,
        expiration_days: int | None = None,
        is_logging_bucket: bool = False,
        server_access_logs_bucket: s3.IBucket | None = None,
    ) -> None:
        super().__init__(scope, id)

        self._config = config
        self._component = component
        self._read_policy: iam.ManagedPolicy | None = None
        self._read_write_policy: iam.ManagedPolicy | None = None

        removal_policy = REMOVAL_POLICY_MAP[config.removal_policy]

        # If a customer-managed key is supplied, encrypt with it; otherwise fall
        # back to the config-driven encryption mode.
        if encryption_key is not None:
            encryption = s3.BucketEncryption.KMS
        else:
            encryption = _ENCRYPTION_MAP.get(config.encryption, s3.BucketEncryption.S3_MANAGED)

        should_auto_delete = (
            auto_delete_objects
            if auto_delete_objects is not None
            else config.removal_policy == "destroy"
        )

        # Logging buckets: no versioning (append-only logs), no access logging
        # on themselves (would be circular).
        versioned = False if is_logging_bucket else config.versioning

        lifecycle_rules: list[s3.LifecycleRule] = []
        if not is_logging_bucket:
            lifecycle_rules.append(
                s3.LifecycleRule(
                    noncurrent_version_expiration=cdk.Duration.days(30),
                    enabled=True,
                )
            )
        if expiration_days is not None:
            lifecycle_rules.append(
                s3.LifecycleRule(
                    expiration=cdk.Duration.days(expiration_days),
                    abort_incomplete_multipart_upload_after=cdk.Duration.days(7),
                    enabled=True,
                )
            )

        self._bucket = s3.Bucket(
            self,
            "Bucket",
            bucket_name=config.bucket_name(component),
            encryption=encryption,
            encryption_key=encryption_key,
            versioned=versioned,
            block_public_access=(
                s3.BlockPublicAccess.BLOCK_ALL if config.block_public_access else None
            ),
            removal_policy=removal_policy,
            auto_delete_objects=should_auto_delete,
            enforce_ssl=config.enforce_ssl,
            event_bridge_enabled=event_bridge,
            lifecycle_rules=lifecycle_rules or None,
            server_access_logs_bucket=None if is_logging_bucket else server_access_logs_bucket,
            server_access_logs_prefix=(
                f"{component}/" if (server_access_logs_bucket and not is_logging_bucket) else None
            ),
        )

        if is_logging_bucket:
            cdk_nag.NagSuppressions.add_resource_suppressions(
                self._bucket,
                suppressions=[
                    cdk_nag.NagPackSuppression(
                        id="AwsSolutions-S1",
                        reason=(
                            "This is the access-logging destination bucket. "
                            "Enabling access logging on it would create a "
                            "circular dependency."
                        ),
                    ),
                ],
            )

    @property
    def bucket(self) -> s3.IBucket:
        return self._bucket

    @property
    def read_policy(self) -> iam.ManagedPolicy:
        """Lazily created managed policy granting read access to this bucket."""
        if self._read_policy is None:
            self._read_policy = iam.ManagedPolicy(
                self,
                "ReadPolicy",
                managed_policy_name=self._config.resource_name(self._component, "read-policy"),
                statements=[
                    iam.PolicyStatement(
                        actions=[
                            "s3:GetObject",
                            "s3:ListBucket",
                            "s3:GetBucketLocation",
                        ],
                        resources=[
                            self._bucket.bucket_arn,
                            f"{self._bucket.bucket_arn}/*",
                        ],
                    ),
                ],
            )
        return self._read_policy

    @property
    def read_write_policy(self) -> iam.ManagedPolicy:
        """Lazily created managed policy granting read-write access to this bucket."""
        if self._read_write_policy is None:
            self._read_write_policy = iam.ManagedPolicy(
                self,
                "ReadWritePolicy",
                managed_policy_name=self._config.resource_name(
                    self._component, "read-write-policy"
                ),
                statements=[
                    iam.PolicyStatement(
                        actions=[
                            "s3:GetObject",
                            "s3:ListBucket",
                            "s3:GetBucketLocation",
                            "s3:PutObject",
                            "s3:DeleteObject",
                        ],
                        resources=[
                            self._bucket.bucket_arn,
                            f"{self._bucket.bucket_arn}/*",
                        ],
                    ),
                ],
            )
        return self._read_write_policy
