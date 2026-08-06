# Third-Party Notices

Auto Speech Journal is distributed under the MIT License. The application also uses third-party
software and model artifacts under their respective licenses. The exact locked versions are in
`uv.lock`; redistribution must retain the notices shipped by those projects.

## Python runtime dependencies

| Package | Purpose |
|---|---|
| `pyside6` | Qt GUI and QML runtime |
| `sounddevice` | Audio-device access |
| `soxr` | Audio resampling |
| `numpy` | Numerical processing |
| `sherpa-onnx` | ONNX preview ASR and VAD |
| `faster-whisper` | Whisper integration |
| `huggingface-hub` | Pinned model download and cache |
| `ctranslate2` | Whisper inference runtime |
| `opencc` | Chinese text conversion |
| `soundfile` | FLAC/audio file I/O |
| `tzdata` | Time-zone database |

The optional advanced CUDA source-install path can include `nvidia-cudnn-cu12`,
`nvidia-cublas-cu12`, and `nvidia-cuda-nvrtc-cu12` under NVIDIA's applicable license terms.
Those packages are not downloaded by the normal Setup.

## Build and installer tools

Windows release artifacts are built with PyInstaller and Inno Setup. The installer may include the
Traditional Chinese ISL language file under its upstream terms. These tools are build-time
components and are not model sources.

## Runtime models

The authoritative list is `src/auto_speech_journal/runtime-models-v1.json`. It records the
repository, immutable commit, file path, size, SHA-256, license, and source for each artifact.

| Installed model | Runtime format | Upstream license |
|---|---|---|
| `models/sherpa-onnx-streaming-paraformer-bilingual-zh-en` | Sherpa-ONNX int8 encoder/decoder and tokens | Apache-2.0 |
| `models/silero-vad` | Silero VAD ONNX | MIT |
| `models/faster-whisper-large-v3-turbo` | CTranslate2 model files | MIT |

Setup bundles the CPU application and open-source Python dependencies, but does not bundle or
download models or CUDA runtime. During first run, the App downloads only manifest-pinned,
directly executable files from Hugging Face via `huggingface_hub`. It does not install Torch,
Transformers, or perform Safetensors/model conversion on the user's computer.
