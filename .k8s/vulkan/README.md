# Vulkan Ollama Deployment (GPT‑OSS 120B) on OpenShift

This directory contains a minimal Kustomize stack to deploy an Ollama server using the Vulkan backend on an AMD Strix Halo (gfx1151) system. It is pre‑tuned to run `gpt-oss:120b` by default, but the model is configurable via environment variable.

## What’s included
- Deployment using the custom image: `quay.io/cnuland/vulkan-ollama:latest`
- Vulkan backend enabled via env (`OLLAMA_VULKAN=1`)
- Disk‑backed model store at `/models` (EmptyDir with size limit 200Gi)
- Service (11434/TCP) and an OpenShift Route (edge TLS)
- Conservative pod RAM: `requests: 2Gi`, `limits: 12Gi`
- GPU scheduling: `amd.com/gpu: 1` to ensure /dev/dri is available

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
# Patch to another model, e.g. 20B
oc set env -n gpt-oss deploy/ollama-gpt-oss-120b MODEL_NAME=gpt-oss:20b
oc rollout restart -n gpt-oss deploy/ollama-gpt-oss-120b
```

## Configuration choices (why these values)
- Vulkan backend: `OLLAMA_VULKAN=1` enables Vulkan in Ollama; this path is recommended by AMD for consumer Radeon/iGPU (Strix Halo) and can outperform HIP in some inference workloads.
- Vulkan ICD: Image defaults to RADV (`AMD_VULKAN_ICD=RADV`). To try AMDVLK, set `AMD_VULKAN_ICD=AMDVLK` (ensure AMDVLK is present on the host if required by your environment).
- Device selection: `GGML_VK_VISIBLE_DEVICES=0` is set in the image so device 0 is used by default.
- Pod memory: `requests: 2Gi`, `limits: 12Gi` is enough for downloads, checksums and runtime; keep `/models` on disk to avoid tmpfs RAM spikes.
- Model storage: `/models` uses disk‑backed EmptyDir with `sizeLimit: 200Gi`. For persistence across restarts or node drains, replace with a PVC.
- GPU scheduling: `amd.com/gpu: 1` ensures the pod lands on the AMD GPU node and `/dev/dri` is available to Vulkan.
- Security: runs under OpenShift’s restricted SCC; the image creates `/models` with 0777 so no explicit `fsGroup` is required.

## Using a PVC for persistent models (optional)
Create a PVC and patch the deployment to mount it at `/models`:
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ollama-models
  namespace: gpt-oss
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 250Gi
```
Patch the Deployment:
```bash
oc patch -n gpt-oss deploy/ollama-gpt-oss-120b --type=json -p='[
  {"op":"remove","path":"/spec/template/spec/volumes/0"},
  {"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"models","persistentVolumeClaim":{"claimName":"ollama-models"}}}
]'
```

## Troubleshooting
- `ContainerCreating` for a long time: check image pull permissions and the `quay-pull` secret.
- GPU not available / Pending: ensure no other pod is currently reserving `amd.com/gpu: 1` and the AMD device plugin is healthy.
- System RAM OOMKilled: keep `/models` disk‑backed (not tmpfs), and consider lowering BIOS‑reserved VRAM from 96GiB to ~88GiB to give Linux more RAM.
- Slow first request: the entrypoint pre‑pull is asynchronous via the local API; check `/api/tags` until the model appears.
