SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT := $(shell git rev-parse --show-toplevel)
VENV := $(ROOT)/.venv
CDK := cdk
ENV ?= dev
REGION := $(shell grep CDK_DEPLOY_REGION .env 2>/dev/null | cut -d= -f2)
ACCOUNT := $(shell grep CDK_DEPLOY_ACCOUNT .env 2>/dev/null | cut -d= -f2)
CDK_ENV := PATH="$(VENV)/bin:$(PATH)" CDK_DEPLOY_ACCOUNT=$(ACCOUNT) CDK_DEPLOY_REGION=$(REGION)

# Batch worker image: ECR repo name matches DeploymentConfig.resource_name("worker","repo").
WORKER_REPO := aurora-$(ENV)-worker-repo
WORKER_TAG ?= latest
ECR_REGISTRY := $(ACCOUNT).dkr.ecr.$(REGION).amazonaws.com
WORKER_IMAGE := $(ECR_REGISTRY)/$(WORKER_REPO):$(WORKER_TAG)

WORKER_BUILD_PROJECT := aurora-$(ENV)-worker-image-build
WORKER_SRC_BUCKET := aurora-$(ENV)-worker-src-$(ACCOUNT)-$(REGION)

# CI/CD git source (CodeCommit). Pushing to $(SOURCE_BRANCH) triggers the pipeline.
SOURCE_REPO := aurora-cicd
SOURCE_BRANCH := main
CC_HOST := https://git-codecommit.$(REGION).amazonaws.com
CC_REMOTE_URL := $(CC_HOST)/v1/repos/$(SOURCE_REPO)

.PHONY: help setup test deploy destroy push build-worker build-worker-cloud

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv and install all dependencies
	uv venv $(VENV)
	uv pip install -r requirements-dev.txt --python $(VENV)/bin/python

test: ## Run all quality gates (lint + tests)
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/pytest tests/ -v --tb=short

deploy: test ## Run gates and deploy/update the pipeline stack (then 'make push' to run it)
	$(CDK_ENV) $(CDK) deploy -c env=$(ENV) --require-approval never
	@echo ""
	@echo "Pipeline deployed. Push code to trigger it:  make push"
	@echo "Pipeline console: https://$(REGION).console.aws.amazon.com/codesuite/codepipeline/pipelines/aurora-$(ENV)-cicd-pipeline/view?region=$(REGION)"

push: ## Push the current commit to CodeCommit $(SOURCE_BRANCH) (triggers the pipeline)
	@git -C $(ROOT) remote get-url codecommit >/dev/null 2>&1 || git -C $(ROOT) remote add codecommit $(CC_REMOTE_URL)
	@# Reset the helper list (-c credential.helper=) so a cached osxkeychain entry
	@# can't shadow the AWS CodeCommit credential helper (avoids stale-token 403s).
	git -C $(ROOT) \
	  -c credential.helper= \
	  -c credential.'$(CC_HOST)'.helper='!aws codecommit credential-helper $$@' \
	  -c credential.UseHttpPath=true \
	  push codecommit HEAD:$(SOURCE_BRANCH)
	@echo ""
	@echo "Pushed to CodeCommit $(SOURCE_REPO):$(SOURCE_BRANCH) — the pipeline will start automatically."

destroy: ## Destroy all stacks (everything gets deleted)
	$(CDK_ENV) $(CDK) destroy -c env=$(ENV) --all --force

build-worker: ## Build the Batch worker image and push to ECR (needs Docker + deployed inference stack)
	@[ -n "$(ACCOUNT)" ] && [ -n "$(REGION)" ] || { echo "Set CDK_DEPLOY_ACCOUNT/REGION in .env"; exit 1; }
	aws ecr get-login-password --region $(REGION) \
	  | docker login --username AWS --password-stdin $(ECR_REGISTRY)
	docker build --platform linux/amd64 \
	  -f $(ROOT)/infra/docker/worker/Dockerfile -t $(WORKER_IMAGE) $(ROOT)
	docker push $(WORKER_IMAGE)
	@echo ""
	@echo "Pushed $(WORKER_IMAGE)"
	@echo "The inference job definition uses tag '$(WORKER_TAG)' from repo '$(WORKER_REPO)'."

build-worker-cloud: ## Build the worker image in AWS CodeBuild (no local Docker)
	@[ -n "$(ACCOUNT)" ] && [ -n "$(REGION)" ] || { echo "Set CDK_DEPLOY_ACCOUNT/REGION in .env"; exit 1; }
	cd $(ROOT) && zip -qr /tmp/worker-src.zip aurora-overlay infra/docker -x '*/__pycache__/*'
	aws s3 cp /tmp/worker-src.zip s3://$(WORKER_SRC_BUCKET)/worker-src.zip --region $(REGION)
	aws codebuild start-build --project-name $(WORKER_BUILD_PROJECT) --region $(REGION) \
	  --query "build.id" --output text
	@echo ""
	@echo "Build started. Watch logs in the CodeBuild console (project $(WORKER_BUILD_PROJECT))."
