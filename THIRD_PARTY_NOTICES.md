# Third-Party Notices

Auto Speech Journal 自有程式碼與專案自有 UI／場景資產採 [MIT License](LICENSE)。本文件
記錄 `0.1.0` 直接使用的第三方套件、選用安裝元件及下載模型。實際授權文字以各上游
發行物內的 `LICENSE`、`NOTICE` 與套件 metadata 為準。

GitHub release 的 wheel 與 source distribution **不內含** Python 相依套件、CUDA runtime
或模型權重；安裝程序會從各自來源另行取得它們。每個已安裝 distribution 所附的完整
授權與第三方聲明仍須保留。

## Runtime dependencies

| Component | Pinned version | License | Purpose / upstream |
| --- | --- | --- | --- |
| PySide6 | 6.11.1 | LGPL-3.0-only / GPL / commercial options | Qt UI bindings; [Qt for Python](https://doc.qt.io/qtforpython-6/) |
| sounddevice | 0.5.5 | MIT | WASAPI capture; [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) |
| soxr | 1.1.0 | LGPL-2.1-or-later | Sample-rate conversion; [python-soxr](https://github.com/dofuuz/python-soxr) |
| numpy | 2.4.6 | BSD-3-Clause plus bundled notices | Audio arrays; [NumPy](https://numpy.org/) |
| sherpa-onnx | 1.13.4 | Apache-2.0 | Streaming ASR and VAD runtime; [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) |
| faster-whisper | 1.2.1 | MIT | Final transcription integration; [faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| ctranslate2 | 4.8.1 | MIT | Whisper inference runtime; [CTranslate2](https://github.com/OpenNMT/CTranslate2) |
| OpenCC | 1.4.1 | Apache-2.0 | Traditional Chinese normalization; [OpenCC](https://github.com/BYVoid/OpenCC) |
| soundfile | 0.13.1 | BSD-3-Clause; bundled libsndfile has its own LGPL terms | FLAC I/O; [python-soundfile](https://github.com/bastibe/python-soundfile) |
| tzdata | `>=2025.2,<2027` | Apache-2.0; IANA data has separate terms | Windows timezone data; [python-tzdata](https://github.com/python/tzdata) |

PySide6 installs PySide6-Essentials, PySide6-Addons and shiboken6, and several runtime packages
install their own transitive dependencies. Their notices remain in their installed distributions.

## Optional CUDA dependencies

| Component | Pinned version | License / source |
| --- | --- | --- |
| nvidia-cudnn-cu12 | 9.24.0.43 | NVIDIA Software License Agreement; [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) |
| nvidia-cublas-cu12 | 12.9.2.10 | NVIDIA Software License Agreement; [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) |
| nvidia-cuda-nvrtc-cu12 | 12.9.86 | NVIDIA Software License Agreement; [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) |

## Optional model-build dependencies

| Component | Pinned version | License / upstream |
| --- | --- | --- |
| huggingface-hub | 1.23.0 | Apache-2.0; [Hugging Face Hub](https://github.com/huggingface/huggingface_hub) |
| safetensors | 0.8.0 | Apache-2.0; [safetensors](https://github.com/huggingface/safetensors) |
| torch | 2.6.0+cpu | BSD-3-Clause; [PyTorch](https://github.com/pytorch/pytorch) |
| transformers | 5.13.1 | Apache-2.0; [Transformers](https://github.com/huggingface/transformers) |

Development-only tools such as Pytest, Ruff, pre-commit and Pillow are not installed into the
application release environment and retain the licenses distributed by their own packages.

## Downloaded models

| Model | Pinned source | License | Local handling |
| --- | --- | --- | --- |
| `sherpa-onnx-streaming-paraformer-bilingual-zh-en-int8` | GitHub release asset `155855418`; SHA-256 `5462a1fce42693deae572af1e8c4687124b12aa85fe61ff4d3168bb5280e205f` | Apache-2.0, as declared by the archive README | Downloaded to `models\sherpa-onnx-streaming-paraformer-bilingual-zh-en` |
| `sherpa-onnx-silero-vad` | GitHub release asset `271935959`; SHA-256 `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6` | MIT; [Silero VAD license](https://github.com/snakers4/silero-vad/blob/master/LICENSE) | Downloaded to `models\silero-vad` |
| OpenAI Whisper large-v3-turbo | Hugging Face revision `41f01f3fe87f28c78e2fbf8b568835947dd65ed9`; source model SHA-256 `542566a422ae4f3fd23f1ba11add198fca01bbf82e66e6a2857b3f608b1eb9d1` | MIT; [model card](https://huggingface.co/openai/whisper-large-v3-turbo) | Downloaded, hash-checked and converted locally to CTranslate2 float16 format |

模型輸出不會改變模型本身的授權。使用者若重新散布模型、Qt binaries、CUDA 元件或其他
第三方檔案，必須另外遵守相應授權及 notice 義務。

## Local fonts and user content

個人字體不屬於 package assets，wheel 會排除 `assets/fonts`。使用者自行放入 runtime 字體
目錄的檔案，以及使用者的錄音、逐字稿與 Markdown，均不因本專案的 MIT License 而被
重新授權。
