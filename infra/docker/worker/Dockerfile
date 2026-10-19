# Prebaked Aurora editor worker for AWS Batch (GPU).
#
# The image bakes in the full Python environment (PyTorch, flash-attn, Aurora
# deps) so nothing is installed at run time — this is the main lever for fast
# worker spin-up. Model weights are NOT baked in; they are mounted from EFS at
# /models at run time.
#
# Build + push (build context is the REPO ROOT so the aurora-overlay/ files are
# available to COPY on top of the upstream Aurora clone):
#   aws ecr get-login-password --region us-east-2 \
#     | docker login --username AWS --password-stdin <acct>.dkr.ecr.us-east-2.amazonaws.com
#   docker build -f infra/docker/worker/Dockerfile -t aurora-worker .
#   docker tag aurora-worker:latest <repo-uri>:latest
#   docker push <repo-uri>:latest
#
# Then set CONFIG.worker_image_uri (or the WorkerRepo tag) and redeploy the
# inference stack.

# CUDA *devel* base: ships nvcc, which flash-attn needs to build against torch.
# Pull from NVIDIA's registry (nvcr.io) rather than Docker Hub: same image/tags,
# but not subject to Docker Hub's anonymous pull rate limit (429s) that shared
# CI egress IPs hit. Overridable via --build-arg CUDA_IMAGE=...
ARG CUDA_IMAGE=nvcr.io/nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04
FROM ${CUDA_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    CUDA_HOME=/usr/local/cuda \
    AURORA_DIR=/opt/aurora/Aurora \
    MODELS_DIR=/models

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 python3-pip git ffmpeg awscli ca-certificates build-essential ninja-build \
    && rm -rf /var/lib/apt/lists/*

# Clone Aurora at a pinned commit (matches the blog's setup.sh).
ARG AURORA_COMMIT=main
RUN git clone https://github.com/yeates/Aurora.git ${AURORA_DIR} \
    && cd ${AURORA_DIR} && git checkout ${AURORA_COMMIT}

# Apply our overlay on top of the upstream clone (mirrors scripts/setup.sh):
# the editor entrypoint (run_single_sample.py), batch helper, and the hosted
# planner backends including the Nova BedrockAgent (aurora/agent_api.py).
# NOTE: build context must be the repo root (see build command above).
COPY aurora-overlay/aurora/agent_api.py   ${AURORA_DIR}/aurora/agent_api.py
COPY aurora-overlay/run_single_sample.py  ${AURORA_DIR}/run_single_sample.py
COPY aurora-overlay/batch_ad_edits.py     ${AURORA_DIR}/batch_ad_edits.py

# Dependencies mirror Aurora's setup_env.sh (inference subset; deepspeed/wandb/
# lmdb are training-only and omitted). This MIRRORS setup_env.sh: the install
# ORDER is what avoids dependency conflicts. Aurora's declared deps are loose
# (unpinned transformers, torch>=2.0.0), so the editable package is installed
# LAST — after every pin is already satisfied — instead of letting pip resolve
# and pull conflicting versions up front.
# 0) Modern pip/setuptools/wheel (base image ships versions too old for the
#    PEP 660 editable install at the end).
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel
# 1) PyTorch (cu124; flash-attn is pinned to this ABI — do not bump it alone).
RUN pip3 install --no-cache-dir \
        torch==2.5.0 torchvision==0.20.0 torchaudio==2.5.0 \
        --index-url https://download.pytorch.org/whl/cu124
# 2) Core deps in one shot (modelscope first; it may pull the wrong transformers).
RUN pip3 install --no-cache-dir \
        accelerate pyyaml modelscope imageio imageio-ffmpeg einops \
        safetensors sentencepiece protobuf pandas peft datasets \
        huggingface-hub==0.34
# 3) Pin transformers AFTER modelscope (overrides whatever modelscope pulled).
RUN pip3 install --no-cache-dir transformers==5.3.0
# 4) flash-attn MUST match torch 2.5.0 (-> 2.7.3); build against the devel CUDA.
RUN pip3 install --no-cache-dir flash-attn==2.7.3 --no-build-isolation
# 5) Evaluation / runtime deps. setuptools<70 (ViCLIP uses pkg_resources.packaging,
#    removed in >=70); opencv-headless for aurora.agent's cv2 frame sampling; boto3
#    for the worker's AWS calls.
RUN pip3 install --no-cache-dir \
        decord openai ftfy "setuptools<70" opencv-python-headless boto3
# 6) Aurora packages LAST (diffsynth + aurora + evaluation) + the diffusers extra.
#    Everything above already satisfies Aurora's loose deps, so pip links the
#    editable package without re-resolving them. --no-build-isolation reuses the
#    setuptools installed above (PEP 660 capable).
RUN pip3 install --no-cache-dir --no-build-isolation -e "${AURORA_DIR}[diffusers]"

# The Batch entrypoint (build context is the repo root).
COPY infra/docker/worker/run_edit_job.py /opt/run_edit_job.py

# Point AURORA_MODEL_DIR at the EFS mount so the pipeline loads weights from EFS.
ENV AURORA_MODEL_DIR=/models

# Run as a non-root user (least privilege). The EFS model store is mounted
# read-only and world-readable (access point 0755), so a non-root user can
# still load weights; job scratch goes to /tmp which is world-writable.
RUN useradd --create-home --uid 1000 aurora \
    && chown -R aurora:aurora /opt
USER aurora

ENTRYPOINT ["python3", "/opt/run_edit_job.py"]
