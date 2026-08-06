# Third-Party Notices

Auto Speech Journal 自有程式碼與專案自有 UI／場景資產採 [MIT License](LICENSE)。本文件
記錄 `0.2.0` 直接使用的第三方套件、選用安裝元件及下載模型。實際授權文字以各上游
發行物內的 `LICENSE`、`NOTICE` 與套件 metadata 為準。

GitHub release 的 wheel 與 source distribution **不內含** Python 相依套件、CUDA runtime
或模型權重。簽章 Windows Setup 以 PyInstaller 內含 CPU 執行時與本文件；模型由安裝／
修復流程直接從 Hugging Face 的固定 commit revision 下載，CUDA runtime 則由另一份 manifest
從固定 NVIDIA PyPI wheels 下載。兩者都會驗證大小與 SHA-256。

## Runtime dependencies

| Component | Pinned version | License | Purpose / upstream |
| --- | --- | --- | --- |
| PySide6 | 6.11.1 | LGPL-3.0-only / GPL / commercial options | Qt UI bindings; [Qt for Python](https://doc.qt.io/qtforpython-6/) |
| sounddevice | 0.5.5 | MIT | WASAPI capture; [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) |
| soxr | 1.1.0 | LGPL-2.1-or-later | Sample-rate conversion; [python-soxr](https://github.com/dofuuz/python-soxr) |
| numpy | 2.4.6 | BSD-3-Clause plus bundled notices | Audio arrays; [NumPy](https://numpy.org/) |
| ONNX Runtime | 1.27.0 | MIT | Native ONNX execution used by sherpa-onnx; [ONNX Runtime](https://github.com/microsoft/onnxruntime) |
| sherpa-onnx | 1.13.4 | Apache-2.0 | Streaming ASR and VAD runtime; [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) |
| faster-whisper | 1.2.1 | MIT | Final transcription integration; [faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| huggingface-hub | 1.23.0 | Apache-2.0 | Local model-file helpers required by faster-whisper; [Hugging Face Hub](https://github.com/huggingface/huggingface_hub) |
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

Development-only tools such as Pytest, Ruff, pre-commit and Pillow are not installed into the
application release environment and retain the licenses distributed by their own packages.
Torch、Transformers 與 Safetensors 不屬於安裝器、runtime 或模型供應流程；Setup 與
`repair models` 不會安裝或執行它們，也不會在使用者電腦進行模型轉換。

## Installer build components

| Component | Pinned version / source | License | Use |
| --- | --- | --- | --- |
| PyInstaller | 6.16.0 | GPL-2.0-or-later with the PyInstaller bootloader exception | Builds the redistributable onedir runtime |
| Inno Setup | 6.7.3 | Inno Setup License | Builds the per-user Setup executable |
| Traditional Chinese Inno messages | `jrsoftware/issrc@6ef32198ef1f7b7b375cd4b6b90896c2a58eb4c2` | Inno Setup License; translator attribution retained in the vendored file | Provides the `zh-TW` Setup interface |

The vendored translation and its full redistribution terms are in
`packaging/windows/languages/ChineseTraditional.isl`. These build components are not installed as
Python packages in the application runtime; the generated PyInstaller bootloader and Inno Setup
binary remain subject to their upstream terms.

## Downloaded models

| Model | Pinned source | License | Local handling |
| --- | --- | --- | --- |
| `sherpa-onnx-streaming-paraformer-bilingual-zh-en-int8` | [`csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en`](https://huggingface.co/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/tree/8e40c43232a1c5c66c82111efc5820d3accca11b) @ `8e40c43232a1c5c66c82111efc5820d3accca11b` | Apache-2.0 | Directly downloads `encoder.int8.onnx`, `decoder.int8.onnx`, and `tokens.txt` to `models\sherpa-onnx-streaming-paraformer-bilingual-zh-en` |
| Whisper large-v3-turbo (CTranslate2 float16) | [`mobiuslabsgmbh/faster-whisper-large-v3-turbo`](https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/tree/0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf) @ `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` | MIT | Directly downloads `config.json`, `model.bin`, `preprocessor_config.json`, `tokenizer.json`, and `vocabulary.json` to `models\faster-whisper-large-v3-turbo` |
| Sherpa Silero VAD | [`R4kSo1997/sherpa-onnx-silero-vad-v5`](https://huggingface.co/R4kSo1997/sherpa-onnx-silero-vad-v5/tree/4a6e5a75370a3ca741c950f8feda0dbed11c18ac) @ `4a6e5a75370a3ca741c950f8feda0dbed11c18ac` | MIT; [upstream Silero VAD license](https://github.com/snakers4/silero-vad/blob/be95df9152c0d7618fa1edfeb296fc3dae32376f/LICENSE) | Directly downloads the exact existing Sherpa Silero VAD v4 `silero_vad.onnx` runtime artifact to `models\silero-vad` |

`packaging/manifests/runtime-models-v1.json` records each repository, full revision, file path,
byte size, SHA-256, license, and source URL. These are ready-to-run ONNX/CTranslate2 files; no
archive repackaging or local conversion is part of installation or repair.

The test-only reference recording at `tests/fixtures/reference_zh_paraformer_2.wav` is
`test_wavs/2.wav` from the pinned Paraformer repository and revision above (Apache-2.0). Its
source path, SHA-256, reviewed transcripts, and transcript hashes are locked in
`packaging/models/reference-audio-gate.json`; it is not included in the frozen application payload.

模型輸出不會改變模型本身的授權。使用者若重新散布模型、Qt binaries、CUDA 元件或其他
第三方檔案，必須另外遵守相應授權及 notice 義務。

## Local fonts and user content

個人字體不屬於 package assets，wheel 會排除 `assets/fonts`。使用者自行放入 runtime 字體
目錄的檔案，以及使用者的錄音、逐字稿與 Markdown，均不因本專案的 MIT License 而被
重新授權。
