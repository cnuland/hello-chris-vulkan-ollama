#!/usr/bin/env python3
"""Train a microWakeWord model for 'hey shadowbot' on GPU - V2 with augmentation.

Follows the official microWakeWord training pipeline:
  1. Generate TTS samples with piper-sample-generator
  2. Use microWakeWord's Clips, Augmentation, SpectrogramGeneration classes
  3. Download pre-generated negative datasets from HuggingFace
  4. Train with microwakeword.model_train_eval

Environment: pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime container
No system git, wget, curl, or unzip available.
"""
import os
import sys
import subprocess
import logging
import urllib.request
import zipfile
import tarfile
import shutil
import json
import glob

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

WORKSPACE = "/workspace"
OUTPUT = "/output"
PIP_PKGS = os.path.join(WORKSPACE, "pip-pkgs")

# Ensure pip-installed packages are importable
if PIP_PKGS not in sys.path:
    sys.path.insert(0, PIP_PKGS)
# User site-packages (for packages installed with --user)
for _p in glob.glob(f"{WORKSPACE}/local/lib/python*/site-packages"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.makedirs(OUTPUT, exist_ok=True)


def run(cmd, **kwargs):
    log.info(f"RUN: {cmd}")
    env = os.environ.copy()
    user_site = glob.glob(f"{WORKSPACE}/local/lib/python*/site-packages")
    user_site_str = ":".join(user_site) + ":" if user_site else ""
    env["PYTHONPATH"] = f"{user_site_str}{PIP_PKGS}:{WORKSPACE}/piper-sample-generator:{WORKSPACE}/microWakeWord"
    env["PATH"] = f"{WORKSPACE}/local/bin:{PIP_PKGS}/bin:" + env.get("PATH", "")
    kwargs.setdefault("env", env)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    if result.stdout:
        log.info(result.stdout[-2000:])
    if result.returncode != 0:
        log.error(f"FAILED (rc={result.returncode}): {result.stderr[-2000:]}")
        raise RuntimeError(f"Command failed: {cmd}")
    return result


def pip_install(*packages):
    pkg_str = " ".join(packages)
    run(f"pip install --no-build-isolation --target={PIP_PKGS} {pkg_str}")


def pip_install_user(*packages):
    """Install packages that need build isolation"""
    pkg_str = " ".join(packages)
    env = os.environ.copy()
    env.pop("PIP_TARGET", None)
    env["PYTHONUSERBASE"] = f"{WORKSPACE}/local"
    result = subprocess.run(
        f"pip install --user {pkg_str}",
        shell=True, capture_output=True, text=True, env=env
    )
    if result.stdout:
        log.info(result.stdout[-2000:])
    if result.returncode != 0:
        log.error(f"pip_install_user FAILED: {result.stderr[-2000:]}")
        raise RuntimeError(f"pip install --user failed for: {pkg_str}")
    for p in glob.glob(f"{WORKSPACE}/local/lib/python*/site-packages"):
        if p not in sys.path:
            sys.path.insert(0, p)


def download(url, dest):
    log.info(f"Downloading {url} -> {dest}")
    urllib.request.urlretrieve(url, dest)
    log.info(f"Downloaded {os.path.getsize(dest)} bytes")


# ============================================================
# Step 1: Install dependencies
# ============================================================
log.info("=== Step 1: Installing dependencies ===")

pip_install(
    '"tensorflow==2.16.2"',
    "audiomentations",
    "audio-metadata",
    '"datasets<4.0"',
    "mmap-ninja",
    "pymicro-features",
    "webrtcvad-wheels",
    "ai-edge-litert",
    "soundfile",
    "scipy",
    "pyyaml",
    "librosa",
    "pathvalidate",
)

pip_install_user("piper-tts", "piper-phonemize")

# Download piper-sample-generator source
psg_tarball = os.path.join(WORKSPACE, "psg.tar.gz")
download("https://github.com/rhasspy/piper-sample-generator/archive/refs/heads/master.tar.gz", psg_tarball)
with tarfile.open(psg_tarball) as tar:
    tar.extractall(WORKSPACE)
if os.path.exists(os.path.join(WORKSPACE, "piper-sample-generator-master")):
    shutil.move(os.path.join(WORKSPACE, "piper-sample-generator-master"),
                os.path.join(WORKSPACE, "piper-sample-generator"))

# Download microWakeWord source
mww_tarball = os.path.join(WORKSPACE, "mww.tar.gz")
download("https://github.com/kahrendt/microWakeWord/archive/refs/heads/main.tar.gz", mww_tarball)
with tarfile.open(mww_tarball) as tar:
    tar.extractall(WORKSPACE)
for name in ["microWakeWord-main", "micro-wake-word-main"]:
    src = os.path.join(WORKSPACE, name)
    if os.path.exists(src):
        shutil.move(src, os.path.join(WORKSPACE, "microWakeWord"))
        break

# Add microWakeWord to Python path
sys.path.insert(0, os.path.join(WORKSPACE, "microWakeWord"))

# Patch microWakeWord for numpy/keras compatibility
log.info("=== Patching microWakeWord for compatibility ===")
for py_file in glob.glob(os.path.join(WORKSPACE, "microWakeWord", "**", "*.py"), recursive=True):
    with open(py_file, "r") as f:
        content = f.read()
    modified = False
    # TF 2.16 uses numpy 1.x which has trapz, not trapezoid
    if "np.trapezoid(" in content or "numpy.trapezoid(" in content:
        content = content.replace("np.trapezoid(", "np.trapz(")
        content = content.replace("numpy.trapezoid(", "numpy.trapz(")
        modified = True
    # Keras 3 (even with TF 2.16) returns numpy arrays from evaluate(), not tensors
    if ".numpy()" in content:
        content = content.replace(".numpy()", "")
        modified = True
    if modified:
        with open(py_file, "w") as f:
            f.write(content)
        log.info(f"Patched: {py_file}")


# ============================================================
# Step 2: Download Piper TTS voice models
# ============================================================
log.info("=== Step 2: Downloading TTS models ===")

psg_model_dir = os.path.join(WORKSPACE, "piper-models")
os.makedirs(psg_model_dir, exist_ok=True)

HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
voices = [
    ("en_US-lessac-medium", f"{HF_BASE}/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
                            f"{HF_BASE}/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"),
    ("en_US-libritts_r-medium", f"{HF_BASE}/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx",
                                 f"{HF_BASE}/en/en_US/libritts_r/medium/en_US-libritts_r-medium.onnx.json"),
    ("en_GB-alba-medium", f"{HF_BASE}/en/en_GB/alba/medium/en_GB-alba-medium.onnx",
                           f"{HF_BASE}/en/en_GB/alba/medium/en_GB-alba-medium.onnx.json"),
]

psg_models = []
for voice_name, onnx_url, json_url in voices:
    voice_dir = os.path.join(psg_model_dir, voice_name)
    os.makedirs(voice_dir, exist_ok=True)
    onnx_path = os.path.join(voice_dir, f"{voice_name}.onnx")
    json_path = os.path.join(voice_dir, f"{voice_name}.onnx.json")
    try:
        download(onnx_url, onnx_path)
        download(json_url, json_path)
        psg_models.append(onnx_path)
        log.info(f"Downloaded voice: {voice_name}")
    except Exception as e:
        log.warning(f"Failed to download {voice_name}: {e}")

log.info(f"Found {len(psg_models)} TTS voice models")
if not psg_models:
    raise RuntimeError("No TTS voice models found!")


# ============================================================
# Step 3: Generate positive TTS samples
# ============================================================
log.info("=== Step 3: Generating positive TTS samples ===")

generated_samples_dir = os.path.join(WORKSPACE, "generated_samples")
os.makedirs(generated_samples_dir, exist_ok=True)

wake_phrases = [
    "hey shadowbot",
    "hey shadow bot",
    "hey shadow-bot",
]

for i, psg_model in enumerate(psg_models):
    for phrase in wake_phrases:
        phrase_dir = os.path.join(generated_samples_dir, f"voice_{i}_{phrase.replace(' ', '_')}")
        os.makedirs(phrase_dir, exist_ok=True)
        try:
            cmd = (
                f'cd {WORKSPACE}/piper-sample-generator && '
                f'python -m piper_sample_generator '
                f'--model {psg_model} '
                f'--output-dir {phrase_dir} '
                f'--max-samples 200 '
                f'--batch-size 10 '
                f'"{phrase}"'
            )
            run(cmd)
        except Exception as e:
            log.warning(f"Failed generating samples for '{phrase}' with voice {i}: {e}")

pos_count = 0
for root, dirs, files in os.walk(generated_samples_dir):
    pos_count += len([f for f in files if f.endswith(".wav")])
log.info(f"Generated {pos_count} positive samples")


# ============================================================
# Step 4: Generate synthetic background noise and RIRs
# ============================================================
log.info("=== Step 4: Generating synthetic background noise and RIRs ===")

import importlib
importlib.invalidate_caches()
import numpy as np
import soundfile as sf

SAMPLE_RATE = 16000

# Background noise for augmentation
bg_noise_dir = os.path.join(WORKSPACE, "background_noise")
os.makedirs(bg_noise_dir, exist_ok=True)

def generate_noise(noise_type, sr, duration):
    n = int(sr * duration)
    if noise_type == "white":
        return (np.random.randn(n) * 0.3).astype(np.float32)
    elif noise_type == "pink":
        from scipy.signal import lfilter
        white = np.random.randn(n)
        b = np.array([0.049922035, -0.095993537, 0.050612699, -0.004709510])
        a = np.array([1.0, -2.494956002, 2.017265875, -0.522189400])
        pink = lfilter(b, a, white).astype(np.float32)
        return pink / (np.max(np.abs(pink)) + 1e-8) * 0.3
    elif noise_type == "brown":
        white = np.random.randn(n)
        brown = np.cumsum(white)
        brown = brown / (np.max(np.abs(brown)) + 1e-8) * 0.3
        return brown.astype(np.float32)
    elif noise_type == "babble":
        babble = np.zeros(n, dtype=np.float32)
        for _ in range(5):
            carrier = np.random.randn(n)
            mod_freq = np.random.uniform(2, 6)
            t = np.arange(n) / sr
            envelope = 0.5 * (1 + np.sin(2 * np.pi * mod_freq * t + np.random.uniform(0, 2*np.pi)))
            babble += (carrier * envelope).astype(np.float32)
        return babble / (np.max(np.abs(babble)) + 1e-8) * 0.3

for noise_type in ["white", "pink", "brown", "babble"]:
    for i in range(30):
        duration = np.random.uniform(5, 15)
        noise = generate_noise(noise_type, SAMPLE_RATE, duration)
        sf.write(os.path.join(bg_noise_dir, f"{noise_type}_{i:03d}.wav"), noise, SAMPLE_RATE)

log.info(f"Generated {30 * 4} background noise clips")

# Room Impulse Responses
rir_dir = os.path.join(WORKSPACE, "rir_samples")
os.makedirs(rir_dir, exist_ok=True)

rt60_values = [0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.2]
rir_count = 0
for idx, rt60 in enumerate(rt60_values):
    for variant in range(4):
        rir_len = int(SAMPLE_RATE * rt60 * 1.5)
        rir = np.zeros(rir_len, dtype=np.float32)
        rir[0] = 1.0
        n_reflections = np.random.randint(3, 8)
        for _ in range(n_reflections):
            delay = np.random.randint(int(0.001 * SAMPLE_RATE), int(0.02 * SAMPLE_RATE))
            if delay < rir_len:
                rir[delay] += np.random.choice([-1, 1]) * np.random.uniform(0.1, 0.5)
        decay_rate = -6.908 / (rt60 * SAMPLE_RATE)
        t = np.arange(rir_len)
        reverb_noise = np.random.randn(rir_len).astype(np.float32) * np.exp(decay_rate * t)
        rir += reverb_noise * np.random.uniform(0.02, 0.1)
        rir = (rir / (np.max(np.abs(rir)) + 1e-8)).astype(np.float32)
        sf.write(os.path.join(rir_dir, f"rir_{idx:02d}_{variant}.wav"), rir, SAMPLE_RATE)
        rir_count += 1

log.info(f"Generated {rir_count} synthetic RIRs")


# ============================================================
# Step 5: Generate spectrograms using microWakeWord pipeline
# ============================================================
log.info("=== Step 5: Generating spectrograms with microWakeWord pipeline ===")

from microwakeword.audio.clips import Clips
from microwakeword.audio.augmentation import Augmentation
from microwakeword.audio.spectrograms import SpectrogramGeneration
from mmap_ninja.ragged import RaggedMmap

clips = Clips(
    input_directory=generated_samples_dir,
    file_pattern='**/*.wav',
    max_clip_duration_s=None,
    remove_silence=False,
    random_split_seed=10,
    split_count=0.1,
)

augmenter = Augmentation(
    augmentation_duration_s=3.2,
    augmentation_probabilities={
        "SevenBandParametricEQ": 0.1,
        "TanhDistortion": 0.1,
        "PitchShift": 0.1,
        "BandStopFilter": 0.1,
        "AddColorNoise": 0.1,
        "AddBackgroundNoise": 0.75,
        "Gain": 1.0,
        "RIR": 0.5,
    },
    impulse_paths=[rir_dir],
    background_paths=[bg_noise_dir],
    background_min_snr_db=-5,
    background_max_snr_db=10,
    min_jitter_s=0.195,
    max_jitter_s=0.205,
)

positive_features_dir = os.path.join(WORKSPACE, "generated_augmented_features")
os.makedirs(positive_features_dir, exist_ok=True)

splits = ["training", "validation", "testing"]
for split in splits:
    out_dir = os.path.join(positive_features_dir, split)
    os.makedirs(out_dir, exist_ok=True)

    split_name = "train"
    repetition = 2

    spectrograms = SpectrogramGeneration(
        clips=clips,
        augmenter=augmenter,
        slide_frames=10,
        step_ms=10,
    )

    if split == "validation":
        split_name = "validation"
        repetition = 1
    elif split == "testing":
        split_name = "test"
        repetition = 1
        spectrograms = SpectrogramGeneration(
            clips=clips,
            augmenter=augmenter,
            slide_frames=1,
            step_ms=10,
        )

    log.info(f"Generating {split} spectrograms (split={split_name}, repeat={repetition})...")
    RaggedMmap.from_generator(
        out_dir=os.path.join(out_dir, 'wakeword_mmap'),
        sample_generator=spectrograms.spectrogram_generator(split=split_name, repeat=repetition),
        batch_size=100,
        verbose=True,
    )

log.info("Positive spectrograms generated")


# ============================================================
# Step 6: Download negative datasets from HuggingFace
# ============================================================
log.info("=== Step 6: Downloading negative datasets ===")

neg_dir = os.path.join(WORKSPACE, "negative_datasets")
os.makedirs(neg_dir, exist_ok=True)

link_root = "https://huggingface.co/datasets/kahrendt/microwakeword/resolve/main/"
neg_files = ['dinner_party.zip', 'dinner_party_eval.zip', 'no_speech.zip', 'speech.zip']

for fname in neg_files:
    zip_path = os.path.join(neg_dir, fname)
    try:
        download(link_root + fname, zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(neg_dir)
        os.remove(zip_path)
        log.info(f"Extracted {fname}")
    except Exception as e:
        log.warning(f"Failed to download/extract {fname}: {e}")

log.info("Negative datasets ready")


# ============================================================
# Step 7: Create training config YAML
# ============================================================
log.info("=== Step 7: Creating training config ===")

import yaml

config = {}
config["window_step_ms"] = 10
config["train_dir"] = os.path.join(WORKSPACE, "trained_model")

config["features"] = [
    {
        "features_dir": positive_features_dir,
        "sampling_weight": 2.0,
        "penalty_weight": 1.0,
        "truth": True,
        "truncation_strategy": "truncate_start",
        "type": "mmap",
    },
]

# Add negative datasets if downloaded
for neg_name in ["speech", "dinner_party", "no_speech"]:
    neg_path = os.path.join(neg_dir, neg_name)
    if os.path.exists(neg_path):
        config["features"].append({
            "features_dir": neg_path,
            "sampling_weight": 10.0 if neg_name != "no_speech" else 5.0,
            "penalty_weight": 1.0,
            "truth": False,
            "truncation_strategy": "random",
            "type": "mmap",
        })

dinner_party_eval = os.path.join(neg_dir, "dinner_party_eval")
if os.path.exists(dinner_party_eval):
    config["features"].append({
        "features_dir": dinner_party_eval,
        "sampling_weight": 0.0,
        "penalty_weight": 1.0,
        "truth": False,
        "truncation_strategy": "split",
        "type": "mmap",
    })

config["training_steps"] = [15000]
config["positive_class_weight"] = [1]
config["negative_class_weight"] = [20]
config["learning_rates"] = [0.001]
config["batch_size"] = 128
config["time_mask_max_size"] = [0]
config["time_mask_count"] = [0]
config["freq_mask_max_size"] = [0]
config["freq_mask_count"] = [0]
config["eval_step_interval"] = 500
config["clip_duration_ms"] = 1500
config["target_minimization"] = 0.9
config["minimization_metric"] = None
config["maximization_metric"] = "average_viable_recall"

config_path = os.path.join(WORKSPACE, "training_parameters.yaml")
with open(config_path, "w") as f:
    yaml.dump(config, f)

log.info(f"Training config saved to {config_path}")
log.info(f"Feature dirs: {[f['features_dir'] for f in config['features']]}")


# ============================================================
# Step 8: Train the model
# ============================================================
log.info("=== Step 8: Training MixConv model ===")

train_cmd = (
    f'cd {WORKSPACE}/microWakeWord && '
    f'python -m microwakeword.model_train_eval '
    f'--training_config={config_path} '
    f'--train 1 '
    f'--restore_checkpoint 1 '
    f'--test_tf_nonstreaming 0 '
    f'--test_tflite_nonstreaming 0 '
    f'--test_tflite_nonstreaming_quantized 0 '
    f'--test_tflite_streaming 0 '
    f'--test_tflite_streaming_quantized 1 '
    f'--use_weights "best_weights" '
    f'mixednet '
    f'--pointwise_filters "64,64,64,64" '
    f'--repeat_in_block "1,1,1,1" '
    f"--mixconv_kernel_sizes '[5],[7,11],[9,15],[23]' "
    f'--residual_connection "0,0,0,0" '
    f'--first_conv_filters 32 '
    f'--first_conv_kernel_size 5 '
    f'--stride 3 '
)
run(train_cmd)


# ============================================================
# Step 9: Copy outputs
# ============================================================
log.info("=== Step 9: Copying outputs ===")

model_dir = config["train_dir"]
tflite_path = os.path.join(OUTPUT, "hey_shadowbot.tflite")

# Find the TFLite model
found = False
for root, dirs, files in os.walk(model_dir):
    for f in files:
        if f.endswith(".tflite"):
            src = os.path.join(root, f)
            shutil.copy2(src, tflite_path)
            log.info(f"Copied TFLite: {src} -> {tflite_path}")
            found = True
            break
    if found:
        break

if not found:
    log.error("No TFLite model found!")
    # List what's in model dir for debugging
    for root, dirs, files in os.walk(model_dir):
        for f in files:
            log.info(f"  {os.path.join(root, f)}")
    sys.exit(1)

# Create manifest
manifest = {
    "type": "micro",
    "wake_word": "Hey Shadowbot",
    "author": "Chris Nuland",
    "website": "https://github.com/cnuland/hello-chris-vulkan-ollama",
    "model": "hey_shadowbot.tflite",
    "trained_languages": ["en"],
    "version": 2,
    "micro": {
        "probability_cutoff": 0.5,
        "feature_step_size": 10,
        "sliding_window_size": 5,
        "tensor_arena_size": 26080,
        "minimum_esphome_version": "2024.7.0",
    },
}

manifest_path = os.path.join(OUTPUT, "hey_shadowbot.json")
with open(manifest_path, "w") as f:
    json.dump(manifest, f, indent=2)

log.info(f"=== Training complete! ===")
log.info(f"Model: {tflite_path} ({os.path.getsize(tflite_path)} bytes)")
log.info(f"Manifest: {manifest_path}")
for f in os.listdir(OUTPUT):
    log.info(f"  {f} ({os.path.getsize(os.path.join(OUTPUT, f))} bytes)")
