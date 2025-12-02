# GPT-OSS Ollama Deployment - Current State

This document outlines how to recreate the current Ollama deployment running on the OpenShift SNO cluster (OKD) at `ironman.cjlabs.dev`.

> **Note:** This deployment uses Ollama with Vulkan backend, not vLLM with ROCm. The vLLM configuration is preserved in `.k8s/rocm/` for reference.

## Overview

| Component | Value |
|-----------|-------|
| **Ollama Deployment** | `ollama-gpt-oss-120b` |
| **Frontend Deployment** | `gpt-workspace` |
| **Namespace (Ollama)** | `gpt-oss` |
| **Namespace (Frontend)** | `gpt-workspace` |
| **Model** | `gpt-oss:120b` (MXFP4 quantization) |
| **Container Image** | `quay.io/cnuland/vulkan-ollama:latest` |
| **Backend** | Vulkan (Mesa RADV) |
| **GPU Architecture** | AMD Strix Halo 300 series (gfx1151 / RDNA 3.5) |
| **Context Length** | 32K tokens |

## Prerequisites

### 1. OpenShift/OKD Cluster Access
```bash
oc login <cluster-api-url>
```

### 2. Create Namespaces
```bash
oc create namespace gpt-oss        # For Ollama server
oc create namespace gpt-workspace  # For chat frontend
```

### 3. Create Image Pull Secret
```bash
oc create secret docker-registry quay-pull \
  --docker-server=quay.io \
  --docker-username=<username> \
  --docker-password=<password> \
  -n gpt-oss
```

## Ollama Deployment Configuration

### Container Image
```
quay.io/cnuland/vulkan-ollama:latest
```

Custom image with Ubuntu 24.04, Mesa RADV drivers, and Ollama.

### Environment Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| `OLLAMA_HOST` | `0.0.0.0` | Listen on all interfaces |
| `OLLAMA_VULKAN` | `1` | Enable Vulkan backend |
| `OLLAMA_MODELS` | `/models` | Model storage path |
| `MODEL_NAME` | `gpt-oss:120b` | Model to load |
| `OLLAMA_PULL_ON_START` | `1` | Auto-pull model on startup |
| `HIP_VISIBLE_DEVICES` | `-1` | Disable ROCm/HIP |
| `ROCR_VISIBLE_DEVICES` | `-1` | Disable ROCr |
| `AMD_VULKAN_ICD` | `RADV` | Use Mesa RADV driver |
| `GGML_VK_VISIBLE_DEVICES` | `0` | GPU device index |
| `OLLAMA_KEEP_ALIVE` | `30m` | Keep model loaded 30 minutes |
| `OLLAMA_LOAD_TIMEOUT` | `20m` | Allow 20 min for model loading |
| `OLLAMA_RUNNER_START_TIMEOUT` | `20m` | Allow 20 min for runner startup |
| `OLLAMA_NUM_PARALLEL` | `1` | Single request at a time |
| `OLLAMA_CONTEXT_LENGTH` | `32768` | 32K context window |

### Resource Requirements
```yaml
resources:
  requests:
    memory: 8Gi
    amd.com/gpu: "1"
  limits:
    memory: 24Gi
    amd.com/gpu: "1"
```

### Volume Mounts
| Volume | Type | Mount Path | Size |
|--------|------|------------|------|
| `models` | PVC (NFS) | `/models` | 250Gi |

**Note**: Model is persisted across pod restarts via PVC.

### Warmup Sidecar
The deployment includes a warmup sidecar that:
1. Waits for Ollama server to be ready
2. Triggers model loading and Vulkan shader compilation
3. Sends keep-alive requests every 20 minutes to prevent unloading

## Deploy Ollama

```bash
# Deploy with Kustomize
oc apply -k .k8s/vulkan

# Wait for pod to be ready
oc get pods -n gpt-oss -l app=ollama-gpt-oss-120b -w
```

## Chat Frontend Configuration

### Build and Deploy
```bash
# Build from local directory
oc start-build gpt-workspace --from-dir=./app --follow -n gpt-workspace

# Set environment variables
oc set env deployment/gpt-workspace \
  OLLAMA_API_BASE=http://ollama-gpt-oss-120b.gpt-oss.svc:11434 \
  DATA_DIR=/tmp/data \
  -n gpt-workspace
```

### Frontend Features
- Streaming chat with COT (chain-of-thought) rendering
- Rolling context window with automatic summarization
- 16K default output tokens for verbose reasoning
- LaTeX math rendering (KaTeX) and syntax highlighting
- Session management, document attachments, prompt presets

## Service Exposure

### Ollama Internal Service
- **Name**: `ollama-gpt-oss-120b`
- **Port**: 11434
- **Type**: ClusterIP

### Ollama External Route
- **URL**: `https://ollama-gpt-oss-120b-gpt-oss.apps.ironman.cjlabs.dev`
- **TLS Termination**: Edge
- **Timeout**: 300s (for model loading)

### Frontend Route
- **URL**: `https://gpt-workspace-gpt-workspace.apps.ironman.cjlabs.dev`

## Verify Deployment

### Check Pod Status
```bash
# Ollama
oc get pods -n gpt-oss -l app=ollama-gpt-oss-120b

# Frontend
oc get pods -n gpt-workspace
```

### Check Logs
```bash
# Ollama main container
oc logs -n gpt-oss deployment/ollama-gpt-oss-120b -c ollama

# Warmup sidecar
oc logs -n gpt-oss deployment/ollama-gpt-oss-120b -c warmup
```

### Test API Endpoints
```bash
# Check version
curl -sS https://ollama-gpt-oss-120b-gpt-oss.apps.ironman.cjlabs.dev/api/version

# List models
curl -sS https://ollama-gpt-oss-120b-gpt-oss.apps.ironman.cjlabs.dev/api/tags | jq .

# Check loaded model
curl -sS https://ollama-gpt-oss-120b-gpt-oss.apps.ironman.cjlabs.dev/api/ps | jq .

# Generate text
curl -sS https://ollama-gpt-oss-120b-gpt-oss.apps.ironman.cjlabs.dev/api/generate \
  -d '{"model":"gpt-oss:120b","prompt":"Hello!","stream":false}' | jq -r .response
```

## Performance Metrics

| Metric | Value |
|--------|-------|
| Model Size | ~65 GiB (MXFP4) |
| VRAM Usage | ~61 GiB |
| CPU RAM Overflow | ~1.1 GiB |
| Prefill Speed | ~300-450 tok/s |
| Decode Speed | ~34-38 tok/s |
| Context Window | 32K tokens |
| Initial Load Time | ~7-8 minutes (shader compilation) |
| Subsequent Requests | <1 second |

## File Structure
```
.
├── app/                         # Next.js chat frontend
│   ├── src/lib/tokens.ts        # Context configuration
│   ├── src/lib/context-manager.ts
│   └── README.md
├── .k8s/
│   ├── vulkan/                  # Current deployment (recommended)
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   ├── route.yaml
│   │   ├── pvc.yaml
│   │   └── kustomization.yaml
│   └── rocm/                    # Legacy vLLM deployment
└── build-ollama-vulkan/         # Container image build
```

## Troubleshooting

### Model takes too long to load
- First load requires ~7-8 minutes for Vulkan shader compilation
- Check warmup sidecar logs: `oc logs -n gpt-oss <pod> -c warmup`
- Ensure route timeout is 300s+

### Response cuts off mid-generation
- Increase `num_predict` in API call
- Frontend defaults to 16K tokens (`defaultOutputTokens` in `tokens.ts`)
- For complex reasoning, may need 24K-32K tokens

### Model keeps unloading
- Check warmup sidecar is running keep-alive loop
- Verify `OLLAMA_KEEP_ALIVE=30m` is set
- Sidecar sends requests every 20 minutes

### Context exceeded errors
- Frontend has automatic summarization at 80% capacity
- Check `[ContextManager]` logs in frontend
- Verify `OLLAMA_CONTEXT_LENGTH=32768`
