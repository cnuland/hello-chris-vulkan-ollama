# Vulkan Ollama on OpenShift (AMD Strix Halo)

Run large language models (GPT-OSS 120B) on AMD integrated GPUs using Ollama with the Vulkan backend on OpenShift/Kubernetes.

## Overview

This project provides container images and Kubernetes manifests to deploy Ollama with Vulkan acceleration on AMD Strix Halo (gfx1151) and similar AMD iGPUs. It's optimized for the OpenAI GPT-OSS models using MXFP4 quantization.

**Key Features:**
- Vulkan backend for AMD consumer GPUs (no ROCm required)
- Optimized for shared memory iGPU configurations
- Pre-configured for GPT-OSS 120B model
- OpenShift/Kubernetes ready with Kustomize

## Performance (AMD Radeon 8060S / Strix Halo)

| Model | Prefill | Decode | VRAM Usage | Initial Load |
|-------|---------|--------|------------|---------------|
| GPT-OSS 120B (MXFP4) | ~300-450 tok/s | ~36-38 tok/s | ~61 GiB | ~7-8 min |
| GPT-OSS 20B (MXFP4) | ~500 tok/s | ~58 tok/s | ~13 GiB | ~10 sec |

**Note:** The 120B model requires ~7-8 minutes for Vulkan shader compilation on the first request after pod startup. Subsequent requests are fast (<1 second) while the model remains loaded (30 minute keep-alive).

## Project Structure

```
├── build-ollama-vulkan/     # Container image build files
│   ├── Containerfile        # Ubuntu 24.04 + Mesa RADV + Ollama
│   └── docker-entrypoint.sh # Startup script with auto model pull
├── .k8s/
│   ├── vulkan/              # Vulkan backend deployment (recommended)
│   │   ├── deployment.yaml  # Ollama pod with GPU resources
│   │   ├── service.yaml     # ClusterIP service
│   │   ├── route.yaml       # OpenShift route (edge TLS)
│   │   ├── pvc.yaml         # Model storage (250Gi NFS)
│   │   └── kustomization.yaml
│   └── rocm/                # ROCm backend deployment (alternative)
└── scripts/
    └── monitor_ollama.sh    # Monitoring helper
```

## Quick Start

### Prerequisites
- OpenShift cluster with AMD GPU node (or any Kubernetes with AMD device plugin)
- `amd.com/gpu` resource available on the node
- ~64 GiB GPU memory for 120B model (shared memory systems need BIOS configuration)

### Deploy

```bash
# Create namespace
oc new-project gpt-oss

# Create image pull secret (if using private registry)
oc create secret docker-registry quay-pull \
  --docker-server=quay.io \
  --docker-username="$QUAY_USERNAME" \
  --docker-password="$QUAY_PASSWORD"

# Deploy with Kustomize
oc apply -k .k8s/vulkan

# Wait for pod to be ready
oc get pods -l app=ollama-gpt-oss-120b -w
```

### Test

```bash
# Get the route URL
ROUTE=$(oc get route ollama-gpt-oss-120b -o jsonpath='{.spec.host}')

# Check API
curl -sS https://$ROUTE/api/version

# List models
curl -sS https://$ROUTE/api/tags | jq .

# Generate text
curl -sS https://$ROUTE/api/generate \
  -d '{"model":"gpt-oss:120b","prompt":"Hello!","stream":false}' | jq -r .response
```

## Container Image

The custom Ollama image is built on Ubuntu 24.04 with:
- Mesa RADV drivers (via kisak-mesa PPA) for Vulkan support
- Ollama server binary
- Auto model pull on startup

### Build

```bash
podman build -t quay.io/cnuland/vulkan-ollama:latest ./build-ollama-vulkan
podman push quay.io/cnuland/vulkan-ollama:latest
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_NAME` | `gpt-oss:120b` | Model to pull on startup |
| `OLLAMA_VULKAN` | `1` | Enable Vulkan backend |
| `OLLAMA_PULL_ON_START` | `1` | Auto-pull model on container start |
| `AMD_VULKAN_ICD` | `RADV` | Vulkan driver (RADV or AMDVLK) |
| `GGML_VK_VISIBLE_DEVICES` | `0` | GPU device index |
| `OLLAMA_KEEP_ALIVE` | `30m` | Keep model loaded between requests |
| `OLLAMA_RUNNER_START_TIMEOUT` | `10m` | Timeout for Vulkan shader compilation |
| `OLLAMA_CONTEXT_LENGTH` | `2048` | Context window size |
| `OLLAMA_FLASH_ATTENTION` | `1` | Enable flash attention |

## Resource Requirements

### GPT-OSS 120B
- **GPU VRAM:** ~60 GiB
- **CPU RAM:** ~1.1 GiB (overflow layers)
- **Disk:** ~65 GiB (model storage)
- **Pod limits:** 4Gi request / 16Gi limit

### GPT-OSS 20B
- **GPU VRAM:** ~13 GiB
- **CPU RAM:** minimal
- **Disk:** ~13 GiB
- **Pod limits:** 2Gi request / 8Gi limit

## Shared Memory Configuration (Strix Halo)

For systems with shared CPU/GPU memory (like AMD Strix Halo), configure BIOS to allocate sufficient VRAM:
- **120B model:** Set GPU memory to 64+ GiB
- **20B model:** 16-32 GiB is sufficient

## References

- [AMD OpenAI Day-0 Guidance](https://rocm.blogs.amd.com/ecosystems-and-partners/openai-day-0/README.html)
- [Run GPT-OSS on AMD Ryzen AI / Radeon](https://www.amd.com/en/blogs/2025/how-to-run-openai-gpt-oss-20b-120b-models-on-amd-ryzen-ai-radeon.html)
- [Ollama GPU Documentation](https://docs.ollama.com/gpu)

## License

MIT
