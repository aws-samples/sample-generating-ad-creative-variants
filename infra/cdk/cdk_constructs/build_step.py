from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk.pipelines as pipelines

if TYPE_CHECKING:
    from deployment_config import DeploymentConfig

# The pipeline source is the whole git repo; the CDK app (and its gate configs)
# live in this subdirectory, so every gate command runs from there.
_CDK_DIR = "infra/cdk"


def _in_cdk(commands: list[str]) -> list[str]:
    """Run each command in a subshell that cds into the CDK app dir.

    CodeBuild *persists* the working directory across phases (install -> build),
    so a bare ``cd`` would compound (the build phase would try to cd into
    infra/cdk/infra/cdk). A subshell scopes the cd to that single command and
    leaves the parent shell at the source root."""
    return [f"(cd {_CDK_DIR} && {c})" for c in commands]


class BuildStep:
    """Standardized CodeBuild step for CI/CD pipeline gates."""

    def __init__(
        self,
        *,
        config: DeploymentConfig,
        step_name: str,
        commands: list[str],
        install_commands: list[str] | None = None,
    ) -> None:
        self._config = config
        self._step_name = step_name
        self._commands = _in_cdk(commands)
        self._install_commands = _in_cdk(install_commands or [])

    @property
    def step(self) -> pipelines.CodeBuildStep:
        return pipelines.CodeBuildStep(
            self._step_name,
            install_commands=self._install_commands,
            commands=self._commands,
        )

    @classmethod
    def quality_gate(cls, config: DeploymentConfig) -> BuildStep:
        return cls(
            config=config,
            step_name="QualityGate",
            install_commands=["pip install -r requirements-dev.txt"],
            commands=[
                "ruff check .",
                "ruff format --check .",
                "mypy --strict .",
            ],
        )

    @classmethod
    def unit_tests(cls, config: DeploymentConfig) -> BuildStep:
        return cls(
            config=config,
            step_name="UnitTests",
            install_commands=[
                "pip install -r requirements.txt -r requirements-dev.txt",
            ],
            commands=["pytest tests/ -v --tb=short"],
        )

    @classmethod
    def security_gate(cls, config: DeploymentConfig) -> BuildStep:
        return cls(
            config=config,
            step_name="SecurityGate",
            install_commands=[
                # TODO: Pin to a full commit SHA instead of a mutable tag for
                # stronger supply-chain guarantees in production.
                "pip install git+https://github.com/awslabs/automated-security-helper.git@v3.5.7",
            ],
            commands=[
                "ash --mode local --source-dir .",
            ],
        )
