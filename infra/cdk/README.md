# Aurora pipeline — CDK app (CI/CD)

AWS CDK application for the Aurora video-editing pipeline, deployed through a
gated CodePipeline. Supersedes the earlier standalone CDK app and the
single-file CloudFormation template (kept for reference in
[`../legacy/`](../legacy/)).

## Layout

```
infra/cdk/
├── app.py                    # entry point (env via -c env=dev|prod)
├── deployment_config.py      # typed config loader (shared.yaml + {env}.yaml + .env)
├── config/                   # shared / dev / prod YAML
├── cdk_constructs/           # one construct per service (KMS, VPC, S3, DynamoDB, EFS, ...)
├── stacks/                   # thin stacks: security, data, pipeline
├── stages/                   # DeployStage (security + data)
├── tests/                    # pytest + cdk assertions
└── scripts/seed_efs.sh       # one-shot EFS model-weights seeder
```

## Stacks deployed by the pipeline

The CodePipeline's `DeployStage` deploys, in order:

1. **SecurityStack** — VPC (isolated subnets + S3/DynamoDB gateway and
   ECR/Logs/STS interface endpoints, **no NAT** — see ADR 0001) and a KMS key.
2. **DataStack** — S3 (landing/staging/results), DynamoDB job table (TTL), and
   the EFS model store + one-shot seeder (ADR 0002).

Retention follows `removal_policy` in config (`destroy` in dev, `retain` in prod).

## Commands (run from repo root)

```bash
make setup     # uv venv at repo root + install dev deps
make test      # ruff + ruff format --check + pytest
make deploy    # gates -> cdk deploy pipeline -> upload source -> pipeline runs
make destroy   # tear everything down
```

`ENV` defaults to `dev`; override with `make deploy ENV=prod`. Deploy target
(account + region) comes from `infra/cdk/.env` (see `.env.example`).

## Pipeline gates

Every deploy is gated (see `docs`/`.claude/rules/pipeline-gates.md`):
`ruff` + `ruff format` + `mypy --strict`, `pytest`, and ASH security scanning.

## Seeding model weights

After the data stack is deployed, seed EFS once:

```bash
./scripts/seed_efs.sh --profile <you> --region <region> --env dev
```

A temporary public-subnet EC2 downloads the weights from Hugging Face onto EFS
and self-terminates. Re-run only if EFS is recreated.
