# vLLM GPT-OSS Current State Documentation

This document outlines how to recreate the current vLLM deployment running on the OpenShift SNO cluster (OKD) at `ironman.cjlabs.dev`.

## Overview

| Component | Value |
|-----------|-------|
| **Deployment Name** | `vllm-gpt-oss-120b` |
| **Namespace** | `gpt-oss` |
| **Model** | `unsloth/gpt-oss-20b-BF16` |
| **Container Image** | `quay.io/cnuland/vllm-gfx1151:rocm71_gfx115x` |
| **vLLM Version** | `0.1.dev9064+g103696862` |
| **GPU Architecture** | AMD Strix Halo 300 series (gfx1151 / RDNA 3.5) |
| **ROCm Version** | 7.1 |

## Prerequisites

### 1. OpenShift/OKD Cluster Access
```bash
oc login <cluster-api-url>
```

### 2. Create Namespace
```bash
oc create namespace gpt-oss
# Or apply the exported namespace YAML:
oc apply -f .k8s/namespace.yaml
```

### 3. Create Required Secrets

#### Hugging Face Token Secret
Create a secret containing your Hugging Face API tokens:
```bash
oc create secret generic hf-token \
  --from-literal=HUGGING_FACE_HUB_TOKEN=<your-hf-token> \
  --from-literal=HF_TOKEN=<your-hf-token> \
  -n gpt-oss
```

Or apply the exported secret (contains base64-encoded values):
```bash
oc apply -f .k8s/secret-hf-token.yaml
```

#### Quay Image Pull Secret
```bash
oc create secret docker-registry quay-pull \
  --docker-server=quay.io \
  --docker-username=<username> \
  --docker-password=<password> \
  -n gpt-oss
```

Or apply the exported secret:
```bash
oc apply -f .k8s/secret-quay-pull.yaml
```

## Deployment Configuration

### Container Image
```
quay.io/cnuland/vllm-gfx1151:rocm71_gfx115x
```

This is a custom-built vLLM image for AMD ROCm targeting `gfx1151` architecture (Strix Halo 300 series).

### vLLM Server Arguments
```bash
python3 -m vllm.entrypoints.openai.api_server \
  --model=unsloth/gpt-oss-20b-BF16 \
  --host=0.0.0.0 \
  --port=8000 \
  --tensor-parallel-size=1 \
  --max-model-len=4096 \
  --max-model-len=16384 \
  --gpu-memory-utilization=0.85 \
  --enforce-eager \
  --disable-custom-all-reduce \
  --trust-remote-code
```

**Note**: There are two `--max-model-len` arguments; the second one (16384) takes precedence.

### Environment Variables

#### Core vLLM Settings
| Variable | Value | Purpose |
|----------|-------|---------|
| `HOME` | `/tmp/vllm-home` | vLLM home directory |
| `HF_HOME` | `/model-cache` | Hugging Face cache location |
| `VLLM_USE_MODELSCOPE` | `false` | Disable ModelScope |
| `VLLM_USE_V1` | `1` | Enable vLLM V1 engine |
| `VLLM_LOGGING_LEVEL` | `DEBUG` | Logging verbosity |

#### ROCm/AMD GPU Settings
| Variable | Value | Purpose |
|----------|-------|---------|
| `ROCM_PATH` | `/opt/rocm` | ROCm installation path |
| `VLLM_TARGET_DEVICE` | `rocm` | Target AMD ROCm platform |
| `VLLM_ROCM_USE_AITER` | `0` | AITER disabled |
| `VLLM_ROCM_CUSTOM_PAGED_ATTN` | `0` | Custom paged attention disabled |
| `VLLM_USE_TRITON_FLASH_ATTN` | `0` | Triton flash attention disabled |
| `NCCL_P2P_DISABLE` | `1` | Disable NCCL P2P |
| `AMD_SERIALIZE_KERNEL` | `3` | Kernel serialization level |
| `HIP_LAUNCH_BLOCKING` | `1` | Synchronous kernel launches |
| `HSA_ENABLE_SDMA` | `0` | Disable SDMA |

#### Python Settings
| Variable | Value | Purpose |
|----------|-------|---------|
| `PYTHONNOUSERSITE` | `1` | Ignore user site-packages |
| `PYTHONPATH` | `/usr/local/lib/python3.12/dist-packages` | Python path |
| `TORCHINDUCTOR_DISABLE` | `1` | Disable TorchInductor |
| `TORCH_LOGS` | `+dynamo` | Torch Dynamo logging |

### Resource Requirements
```yaml
resources:
  requests:
    memory: "16Gi"
    amd.com/gpu: "1"
  limits:
    memory: "32Gi"
    amd.com/gpu: "1"
```

### Volume Mounts
| Volume | Type | Mount Path | Size Limit |
|--------|------|------------|------------|
| `model-cache` | emptyDir | `/model-cache` | 200Gi |
| `shm` | emptyDir (Memory) | `/dev/shm` | 16Gi |

**Important**: Model is downloaded fresh on each pod restart since `emptyDir` is used.

## Deploy the Application

### Apply All Resources
```bash
# Apply in order
oc apply -f .k8s/namespace.yaml
oc apply -f .k8s/secret-hf-token.yaml
oc apply -f .k8s/secret-quay-pull.yaml
oc apply -f .k8s/deployment.yaml
oc apply -f .k8s/service.yaml
oc apply -f .k8s/route.yaml
```

### Or Use Kustomize
Create a `kustomization.yaml` in the `.k8s` directory:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - secret-hf-token.yaml
  - secret-quay-pull.yaml
  - deployment.yaml
  - service.yaml
  - route.yaml
```

Then apply:
```bash
oc apply -k .k8s/
```

## Service Exposure

### Internal Service
- **Name**: `vllm-gpt-oss-120b`
- **Port**: 8000
- **Type**: ClusterIP

### External Route
- **URL**: `https://vllm-gpt-oss-120b-gpt-oss.apps.ironman.cjlabs.dev`
- **TLS Termination**: Edge with Redirect

## Verify Deployment

### Check Pod Status
```bash
oc get pods -n gpt-oss -l app=vllm-gpt-oss-120b
```

### Check Logs
```bash
oc logs -f deployment/vllm-gpt-oss-120b -n gpt-oss
```

### Test API Endpoint
```bash
curl -s https://vllm-gpt-oss-120b-gpt-oss.apps.ironman.cjlabs.dev/v1/models
```

## Model Loading Statistics (from logs)

| Metric | Value |
|--------|-------|
| Model Size | 39.24 GiB |
| Download Time | ~230 seconds |
| Load Time | ~239 seconds |
| Initial Free GPU Memory | 62.39 GiB |
| Available KV Cache | 11.83 GiB |
| Max KV Cache Tokens | 258,368 |
| Max Concurrency (16K tokens) | ~27.82x |

## Known Issues

1. **Naming Mismatch**: Deployment named `vllm-gpt-oss-120b` but runs 20B model
2. **Duplicate max-model-len**: Two arguments specified (4096 and 16384)
3. **No Persistent Storage**: Model re-downloads on pod restart
4. **Missing MoE Config**: Warning about missing MoE config for AMD Radeon Graphics

## File Structure
```
.k8s/
├── namespace.yaml
├── deployment.yaml
├── service.yaml
├── route.yaml
├── secret-hf-token.yaml
└── secret-quay-pull.yaml
```
