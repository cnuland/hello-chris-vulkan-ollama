# Vulkan Ollama Deployment (GPT‑OSS 120B) on OpenShift

This directory contains a minimal Kustomize stack to deploy an Ollama server using the Vulkan backend on an AMD Strix Halo (gfx1151) system. It is pre‑tuned to run `gpt-oss:120b` by default, but the model is configurable via environment variable.

## What's included
- Deployment using the custom image: `quay.io/cnuland/vulkan-ollama:latest`
- **Warmup sidecar** that triggers model loading immediately on pod startup
- Vulkan backend enabled via env (`OLLAMA_VULKAN=1`)
- Disk‑backed model store at `/models` (PVC with 250Gi)
- Service (11434/TCP) and an OpenShift Route (edge TLS, 300s timeout)
- Pod RAM: `requests: 8Gi`, `limits: 24Gi` (120B model requires ~1.1Gi CPU RAM for overflow)
- GPU scheduling: `amd.com/gpu: 1` to ensure /dev/dri is available

## Performance (AMD Radeon 8060S / Strix Halo)
- **Prefill:** ~300-450 tok/s
- **Decode:** ~36-38 tok/s
- **VRAM usage:** ~61 GiB (MXFP4 quantization)
- **Initial load time:** ~7-8 minutes (Vulkan shader compilation on first request)
- **Subsequent requests:** <1 second (model stays loaded for 30 minutes)

**Note:** The warmup sidecar automatically triggers Vulkan shader compilation on pod startup, so the first user request doesn't have to wait. Once loaded, the model stays in memory and responds quickly.

## References (source material used)
- AMD OpenAI Day‑0 guidance (Vulkan + consumer Radeon/Ryzen AI):
  - https://rocm.blogs.amd.com/ecosystems-and-partners/openai-day-0/README.html
- AMD blog: Run OpenAI GPT‑OSS 20B/120B on AMD Ryzen AI / Radeon (MXFP4 via GGUF tools / Vulkan path):
  - https://www.amd.com/en/blogs/2025/how-to-run-openai-gpt-oss-20b-120b-models-on-amd-ryzen-ai-radeon.html
- Ollama GPU guidance (Vulkan support and environment variables):
  - https://docs.ollama.com/gpu

## Prerequisites
- OpenShift project `gpt-oss` (namespace)
- AMD GPU device plugin on the node (so the `amd.com/gpu` resource mounts `/dev/dri`)
- Image pull secret for Quay (if your cluster needs it): secret name `quay-pull` in namespace `gpt-oss`

Create the pull secret if needed:
```bash
oc create secret docker-registry quay-pull \
  --docker-server=quay.io \
  --docker-username="$QUAY_USERNAME" \
  --docker-password="$QUAY_PASSWORD" \
  -n gpt-oss
```

## Deploy
Apply everything with kustomize:
```bash
oc apply -k .k8s/vulkan
```
Wait for the pod to become Ready:
```bash
oc get pods -n gpt-oss -l app=ollama-gpt-oss-120b -w
```
Get the external URL:
```bash
oc get route -n gpt-oss ollama-gpt-oss-120b -o jsonpath='https://{.spec.host}\n'
```

## Quick tests
```bash
# Version endpoint
curl -sS https://$(oc get route -n gpt-oss ollama-gpt-oss-120b -o jsonpath='{.spec.host}')/api/version

# List models (appears after the first pull completes)
curl -sS https://$(oc get route -n gpt-oss ollama-gpt-oss-120b -o jsonpath='{.spec.host}')/api/tags

# Generate (non‑streaming example)
curl -sS -X POST https://$(oc get route -n gpt-oss ollama-gpt-oss-120b -o jsonpath='{.spec.host}')/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-oss:120b","prompt":"Say hello in one short sentence.","stream":false}'
```

## Changing the model
The image defaults to `MODEL_NAME=gpt-oss:120b` and pulls it automatically on start (`OLLAMA_PULL_ON_START=1`). Change it at deploy time or patch later:
```bash
# Patch to another model, e.g. 20B for faster inference
oc set env -n gpt-oss deploy/ollama-gpt-oss-120b MODEL_NAME=gpt-oss:20b
oc rollout restart -n gpt-oss deploy/ollama-gpt-oss-120b
```

## Configuration choices (why these values)
- Vulkan backend: `OLLAMA_VULKAN=1` enables Vulkan in Ollama; this path is recommended by AMD for consumer Radeon/iGPU (Strix Halo) and can outperform HIP in some inference workloads.
- Vulkan ICD: Image defaults to RADV (`AMD_VULKAN_ICD=RADV`). To try AMDVLK, set `AMD_VULKAN_ICD=AMDVLK` (ensure AMDVLK is present on the host if required by your environment).
- Device selection: `GGML_VK_VISIBLE_DEVICES=0` is set in the image so device 0 is used by default.
- Pod memory: `requests: 8Gi`, `limits: 24Gi` provides headroom for the 120B model which offloads ~1.1Gi to CPU RAM.
- Model storage: `/models` uses a 250Gi NFS PVC for persistence across restarts and node drains.
- GPU scheduling: `amd.com/gpu: 1` ensures the pod lands on the AMD GPU node and `/dev/dri` is available to Vulkan.
- Security: runs under OpenShift's restricted SCC; the image creates `/models` with 0777 so no explicit `fsGroup` is required.

## Warmup Sidecar
The deployment includes a warmup sidecar container that:
1. Waits for the Ollama server to be ready
2. Sends a generate request to trigger model loading and Vulkan shader compilation
3. Runs in the background so the pod becomes ready quickly
4. Periodically pings the API to keep the model warm

This ensures users don't have to wait 7-8 minutes on their first request.

## Performance tuning environment variables
- `OLLAMA_KEEP_ALIVE=30m` - Keep model loaded for 30 minutes between requests
- `OLLAMA_LOAD_TIMEOUT=20m` - Allow 20 minutes for model loading (Vulkan shader compilation)
- `OLLAMA_RUNNER_START_TIMEOUT=20m` - Allow 20 minutes for runner startup
- `OLLAMA_CONTEXT_LENGTH=2048` - Reduced context for faster initialization
- `OLLAMA_NUM_PARALLEL=1` - Single request at a time (optimized for single user)

## Model storage
The deployment uses a 250Gi NFS PVC (`ollama-models`) for persistent model storage at `/models`. This is already configured in `pvc.yaml` and mounted in the deployment.

## Troubleshooting
- `ContainerCreating` for a long time: check image pull permissions and the `quay-pull` secret.
- GPU not available / Pending: ensure no other pod is currently reserving `amd.com/gpu: 1` and the AMD device plugin is healthy.
- System RAM OOMKilled: keep `/models` disk‑backed (not tmpfs), and consider lowering BIOS‑reserved VRAM from 96GiB to ~88GiB to give Linux more RAM.
- **Warmup sidecar logs:** Check `oc logs -n gpt-oss <pod> -c warmup` to verify the sidecar triggered model loading.
- **Model loading status:** Check `oc logs -n gpt-oss <pod> -c ollama` to see Vulkan shader compilation progress.
- **First request times out (504):** Ensure the warmup sidecar is running and route timeout is set to 300s+. The 120B model requires ~7-8 minutes for Vulkan shader compilation.
- **Subsequent requests are slow:** Check if the model was unloaded (`curl /api/ps`). Keep-alive is set to 30 minutes by default.
