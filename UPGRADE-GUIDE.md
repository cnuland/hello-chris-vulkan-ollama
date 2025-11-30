# vLLM Upgrade Guide: Enabling MoE and Quantization Support

This guide outlines how to upgrade the current vLLM configuration to improve support for Mixture of Experts (MoE) models and quantization on the AMD Strix Halo 300 series (gfx1151).

## Current State Analysis

### What We Have Now
- **Image**: `quay.io/cnuland/vllm-gfx1151:rocm71_gfx115x` (~2 months old)
- **vLLM Version**: `0.1.dev9064+g103696862`
- **ROCm**: 7.1
- **AITER**: Disabled (`VLLM_ROCM_USE_AITER=0`)
- **MoE Support**: Limited (missing tuned config files)
- **Quantization**: Not configured

### Current Limitations
1. AITER (AI Tensor Engine for ROCm) is disabled
2. Missing MoE configuration file for Radeon Graphics
3. No quantization parameters configured
4. Experimental build that predates latest ROCm optimizations

---

## Recommended Upgrade Paths

### Option 1: Official Navi/RDNA3 Image (Most Stable)

**Image:**
```
rocm/vllm-dev:rocm6.4.2_navi_ubuntu22.04_py3.10_pytorch_2.7_vllm_0.9.2
```

**Pros:**
- Official AMD-maintained image
- Stable, tested release
- vLLM 0.9.2 (newer than current)
- GPTQ quantization support built-in

**Cons:**
- ROCm 6.4.2 (older than current 7.1)
- Built for gfx1100, may need gfx1151 fallback
- AITER optimizations limited

**Deployment Changes:**
```yaml
spec:
  containers:
  - name: vllm
    image: rocm/vllm-dev:rocm6.4.2_navi_ubuntu22.04_py3.10_pytorch_2.7_vllm_0.9.2
    env:
    # Enable gfx1100 fallback for gfx1151
    - name: HSA_OVERRIDE_GFX_VERSION
      value: "11.0.0"
```

---

### Option 2: AITER-Enabled Nightly (Best MoE Support)

**Image:**
```
rocm/vllm-dev:nightly_0624_rc2_0624_rc2_20250620
```

**Pros:**
- Latest AITER MoE optimizations
- Fused MoE kernels for better performance
- More recent vLLM features

**Cons:**
- Nightly build = less stable
- Primarily tested on MI300X datacenter GPUs
- May require additional tuning for consumer GPU

**Deployment Changes:**
```yaml
spec:
  containers:
  - name: vllm
    image: rocm/vllm-dev:nightly_0624_rc2_0624_rc2_20250620
    env:
    # Enable AITER for MoE support
    - name: VLLM_ROCM_USE_AITER
      value: "1"
    - name: VLLM_ROCM_USE_AITER_MOE
      value: "1"
    # Fallback for gfx1151
    - name: HSA_OVERRIDE_GFX_VERSION
      value: "11.0.0"
```

---

### Option 3: Latest Main Nightly (Bleeding Edge)

**Image:**
```
rocm/vllm-dev:nightly_main_20251117
```

**Pros:**
- PyTorch 2.9
- ROCm 7.1
- Most recent features and fixes

**Cons:**
- Potentially unstable
- Untested combinations
- May have breaking changes

---

### Option 4: Build Custom Image with gfx1151 Support

For the most optimized experience, build a custom image using TheRock wheels:

**Dockerfile:**
```dockerfile
FROM rocm/pytorch:rocm6.4_ubuntu22.04_py3.10_pytorch_2.5

# Install gfx1151-specific ROCm packages
RUN pip install --index-url https://rocm.nightlies.amd.com/v2/gfx1151/ \
    "rocm[libraries,devel]"

# Install PyTorch with gfx1151 support
RUN pip install --index-url https://rocm.nightlies.amd.com/v2/gfx1151/ \
    --pre torch torchaudio torchvision

# Clone and build vLLM for RDNA3.5
RUN git clone https://github.com/vllm-project/vllm.git /app/vllm
WORKDIR /app/vllm
RUN PYTORCH_ROCM_ARCH="gfx1151" python setup.py develop
```

---

## Enabling MoE Support

### Environment Variables for MoE
Add these environment variables to your deployment:

```yaml
env:
# Master AITER switch
- name: VLLM_ROCM_USE_AITER
  value: "1"

# Enable AITER MoE kernels
- name: VLLM_ROCM_USE_AITER_MOE
  value: "1"

# For FP8 block-scaled MoE (DeepSeek models)
- name: VLLM_ROCM_USE_AITER_FP8_BLOCK_SCALED_MOE
  value: "1"

# Enable AITER linear operations
- name: VLLM_ROCM_USE_AITER_LINEAR
  value: "1"
```

### MoE Model Configuration
For MoE models like Mixtral or DeepSeek:

```yaml
args:
- --model=mistralai/Mixtral-8x7B-Instruct-v0.1
- --host=0.0.0.0
- --port=8000
- --tensor-parallel-size=1
- --max-model-len=4096
- --gpu-memory-utilization=0.85
- --trust-remote-code
- --enforce-eager
```

### Important Caveat
AITER's fused MoE kernels deliver up to 3x performance boost but are primarily optimized for AMD Instinct MI300X datacenter GPUs. On consumer GPUs like Strix Halo, performance gains may be limited.

---

## Enabling Quantization Support

### GPTQ Quantization (Recommended for RDNA3/3.5)

GPTQ is fully supported on ROCm via HIP-compiled kernels:

```yaml
args:
- --model=TheBloke/Mixtral-8x7B-Instruct-v0.1-GPTQ
- --quantization=gptq
- --dtype=auto
- --host=0.0.0.0
- --port=8000
```

### AWQ Quantization

AWQ with Triton kernels works on ROCm:

```yaml
args:
- --model=TheBloke/Llama-2-70B-Chat-AWQ
- --quantization=awq
- --dtype=auto
```

### FP8 Quantization (Limited on RDNA3)

**Warning**: FP8 KV cache has limitations on RDNA3/gfx1151. Only `fp8e5` is supported, not `fp8e4nv`.

```yaml
args:
- --model=meta-llama/Meta-Llama-3.1-8B-Instruct
- --quantization=fp8
- --kv-cache-dtype=fp8
```

**Known Issue**: Enabling both `--kv-cache-dtype=fp8` and `--enable-prefix-caching` simultaneously will crash on RDNA3.

### INT8 Quantization (bitsandbytes)

```yaml
args:
- --model=meta-llama/Llama-2-7b-chat-hf
- --load-format=bitsandbytes
- --quantization=bitsandbytes
```

---

## Recommended Upgraded Deployment

Here's a complete upgraded deployment manifest:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-gpt-oss-20b
  namespace: gpt-oss
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-gpt-oss-20b
  template:
    metadata:
      labels:
        app: vllm-gpt-oss-20b
    spec:
      containers:
      - name: vllm
        image: rocm/vllm-dev:rocm6.4.2_navi_ubuntu22.04_py3.10_pytorch_2.7_vllm_0.9.2
        command:
        - python3
        - -m
        - vllm.entrypoints.openai.api_server
        args:
        - --model=unsloth/gpt-oss-20b-BF16
        - --host=0.0.0.0
        - --port=8000
        - --tensor-parallel-size=1
        - --max-model-len=16384
        - --gpu-memory-utilization=0.85
        - --enforce-eager
        - --disable-custom-all-reduce
        - --trust-remote-code
        env:
        # Core Settings
        - name: HOME
          value: /tmp/vllm-home
        - name: HF_HOME
          value: /model-cache
        - name: VLLM_USE_V1
          value: "1"
        - name: VLLM_LOGGING_LEVEL
          value: INFO
        
        # ROCm Settings
        - name: ROCM_PATH
          value: /opt/rocm
        - name: VLLM_TARGET_DEVICE
          value: rocm
        
        # AITER for MoE (enable if using MoE models)
        - name: VLLM_ROCM_USE_AITER
          value: "1"
        - name: VLLM_ROCM_USE_AITER_MOE
          value: "1"
        
        # gfx1151 -> gfx1100 fallback
        - name: HSA_OVERRIDE_GFX_VERSION
          value: "11.0.0"
        
        # AMD GPU Tuning
        - name: NCCL_P2P_DISABLE
          value: "1"
        - name: AMD_SERIALIZE_KERNEL
          value: "3"
        - name: HSA_ENABLE_SDMA
          value: "0"
        
        # HF Authentication
        - name: HUGGING_FACE_HUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: hf-token
              key: HUGGING_FACE_HUB_TOKEN
        - name: HF_TOKEN
          valueFrom:
            secretKeyRef:
              name: hf-token
              key: HF_TOKEN
        
        ports:
        - containerPort: 8000
          name: http
          protocol: TCP
        resources:
          requests:
            memory: "16Gi"
            amd.com/gpu: "1"
          limits:
            memory: "32Gi"
            amd.com/gpu: "1"
        volumeMounts:
        - name: model-cache
          mountPath: /model-cache
        - name: shm
          mountPath: /dev/shm
        workingDir: /
      
      imagePullSecrets:
      - name: quay-pull
      
      volumes:
      - name: model-cache
        emptyDir:
          sizeLimit: 200Gi
      - name: shm
        emptyDir:
          medium: Memory
          sizeLimit: 16Gi
```

---

## Performance Considerations for Strix Halo

### Known Performance Issues
1. **HIP vs Vulkan**: HIP backend delivers ~350 tok/s vs ~850 tok/s from Vulkan
2. **gfx1151 Kernels**: Currently 2-6X slower than gfx1100 kernels
3. **Driver Dependencies**: Linux 6.15 shows ~15% improvement over 6.14

### Recommendations
1. **Use gfx1100 fallback**: Set `HSA_OVERRIDE_GFX_VERSION=11.0.0`
2. **Keep `--enforce-eager`**: Required for stability on consumer GPUs
3. **Avoid FP8 KV cache**: Use GPTQ instead for quantization
4. **Monitor memory**: Keep `--gpu-memory-utilization` at 0.85 or lower

### Alternative: Vulkan Backend
For pure inference without advanced features, consider llama.cpp with Vulkan backend:
- Delivers ~850 tok/s vs ~350 tok/s from HIP
- Better compute efficiency on RDNA 3.5
- Limited advanced features (no MoE optimization)

---

## Testing the Upgrade

### 1. Deploy the New Configuration
```bash
oc apply -f .k8s/deployment-upgraded.yaml
```

### 2. Monitor Startup Logs
```bash
oc logs -f deployment/vllm-gpt-oss-20b -n gpt-oss
```

### 3. Verify AITER Activation
Look for these log messages:
```
Using Aiter Flash Attention backend on V1 engine
AITER MoE enabled
```

### 4. Test API
```bash
curl -X POST https://vllm-gpt-oss-20b-gpt-oss.apps.ironman.cjlabs.dev/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "unsloth/gpt-oss-20b-BF16",
    "prompt": "Hello, how are you?",
    "max_tokens": 100
  }'
```

---

## Summary of Changes

| Setting | Current | Recommended |
|---------|---------|-------------|
| Image | `quay.io/cnuland/vllm-gfx1151:rocm71_gfx115x` | `rocm/vllm-dev:rocm6.4.2_navi_ubuntu22.04_py3.10_pytorch_2.7_vllm_0.9.2` |
| VLLM_ROCM_USE_AITER | `0` | `1` |
| VLLM_ROCM_USE_AITER_MOE | Not set | `1` |
| HSA_OVERRIDE_GFX_VERSION | Not set | `11.0.0` |
| Quantization | None | GPTQ recommended |
| max-model-len args | 2 (duplicate) | 1 (16384) |

---

## Next Steps

1. **Test Option 1** (Official Navi image) first for stability
2. If MoE performance is critical, try **Option 2** (AITER nightly)
3. Consider building a **custom gfx1151 image** for long-term use
4. Monitor AMD ROCm releases for improved gfx1151 kernel performance
