# 字幕與音訊保存驗證

本次修復針對 VAD 音訊切片、停止時的待處理音訊，以及熱詞更新造成的預覽中斷。
沒有更換預設模型。音訊保存通過，不代表歌曲辨識準確率或畫面延遲通過。

## 可重跑命令

從專案根目錄執行，明確指定待測音訊與已下載的模型目錄。
工具不啟動麥克風、不讀取使用者設定或日記；報告包含辨識文字，私人音訊的報告
應放在 Git 忽略的 `artifacts/` 下。每份報告使用新檔名，工具拒絕覆寫。

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
$env:PYTHONUTF8 = '1'
$sample = 'D:\test-audio\sample.wav'
$models = 'D:\test-models'
New-Item -ItemType Directory -Force artifacts/caption-check | Out-Null

uv run --no-sync python tools/benchmark_caption_pipeline.py --mode coverage `
  --audio $sample --models-dir $models --output artifacts/caption-check/coverage.json

# 每次只啟動一個測試程序；分別換成 1、2、4。
uv run --no-sync python tools/benchmark_caption_pipeline.py --mode preview `
  --audio $sample --models-dir $models --threads 1 `
  --output artifacts/caption-check/preview-1t.json

uv run --no-sync python tools/benchmark_caption_pipeline.py --mode final `
  --audio $sample --models-dir $models --final-device cpu `
  --output artifacts/caption-check/final-cpu.json

uv run --no-sync python tools/benchmark_caption_pipeline.py --mode preview `
  --silence-seconds 10 --models-dir $models --threads 1 `
  --output artifacts/caption-check/silence-1t.json
```

報告記錄模型、實際匯入的產品模組與工具 SHA-256，以及套件版本。
尤其要設定 `PYTHONPATH=src`；開發環境以 `--no-editable` 安裝，否則可能測到舊套件。

## 指標界線

- `preservation_passed`：原生 VAD 已交出的音訊沒有在 wrapper 切片或暫存 FLAC 保存中
  額外遺失。逐樣本比較使用來源 PCM；FLAC 允許 PCM16 的量化誤差。
- `vad_excluded_ranges`：VAD 沒有選取的範圍，不能直接稱為無人聲或靜音。
  `lost_vocal_samples` 保持未測，避免把音訊秒數當作人聲秒數。
- 預設為加速重播。`wall_seconds` 是引擎運算耗時，`cpu_seconds` 是多執行緒累計 CPU
  時間；兩者不同。RSS 是本次程序的工作集，非整個 App 或 GPU 記憶體。
- `--realtime` 依音訊長度控制送入速度；仍不含實體收音、IPC 或畫面繪製。
  第一個非空文字可能辨識錯誤，不等於第一個正確字。
- `final` 使用實際 VAD、暫存 FLAC 和定稿引擎。它量測離線逐段推論，沒有模擬
  正式工作佇列與 UI；定稿延遲及畫面延遲保留未測。有音訊重疊時不直接對串接
  文字計算 CER，避免重複片段污染評分。
- 人聲起點及逐字稿必須經聽校。`--annotation` JSON 使用工具列出的 PCM 雜湊、
  `manual_verified: true`、可選的 `speech_start_ms`、`speech_end_ms` 及 `reference_text`。
  未核對的歌詞時間標記或模型輸出不可標示為人工確認。

## 2026-09-21 已取得的證據

同一份私人歌曲 PCM 的切片比較，原生 VAD 已選取但被 wrapper 丟掉的音訊，從
463,648 samples（28.978 秒）降為 0；修復後暫存 FLAC 保留全部原生 VAD 音訊。
缺失原為歌曲中的三個區間加總，不能解讀成歌曲開頭 29 秒，也不能直接解讀成
29 秒人聲。原始音訊與文字報告不公開。

同一機器各執行一次 INT8 CPU 預覽重播，結果如下。這批是工具補上原始碼指紋前的
探索量測；保有相同 PCM、模型 hash 與參數，尚不作發布效能保證。數字不含模型載入，
不能代表長時間負載測試或 UI 反應。

| 輸入 | 執行緒 | 運算牆鐘時間（秒） | 累計 CPU 時間（秒） |
| --- | ---: | ---: | ---: |
| 私人歌曲，205.17 秒 | 1 | 18.263 | 17.313 |
| 同一歌曲 | 2 | 12.431 | 35.641 |
| 同一歌曲 | 4 | 9.191 | 61.766 |
| 既有中英混合範例，4.69 秒 | 1 | 0.445 | 0.438 |
| 同一中英範例 | 2 | 0.343 | 1.016 |
| 同一中英範例 | 4 | 0.269 | 1.797 |

三種執行緒的所有文字事件與最終預覽文字一致；各跑 10 秒純靜音均沒有非空文字。
增加執行緒沒有改變本次辨識內容，CPU 用量增加，因此暫時保留產品的預設執行緒數。
歌曲與中英範例沒有可信的人工起點或逐字稿，準確率及人聲起點延遲尚未驗收。
Windows 原生即時字幕亦無可信的同音源比較數字。

另以同一 CPU 定稿模型逐段處理修復前後的 FLAC，各 6 段均成功；純定稿運算合計
77.904 與 96.648 秒。修復後需要處理原先被截掉的音訊，兩者工作量不同，不能將此
耗時差直接解讀成效能退步。音訊包含 1.4 秒預期重疊，未對直接串接文字計算 CER；
更長的辨識文字也不是準確率改善的證明。

補充以 Windows「Microsoft Hanhan Desktop」、Rate 0 產生 16 kHz／PCM16／單聲道
中文合成語音作為受控對照。來源文字為：

> 今天下午三點開會，請先整理上週的錄音筆記。如果有聽不清楚的地方，就把那一句
> 標記起來，等會議結束後再一起確認。

此段合成音長 13.331 秒，採 `--realtime` 送入；以獨立合成音的第一個非零樣本估計
起點，並非人工聽校的真人起點。1／2／4 執行緒到第一個非空引擎文字分別約
771.9／760.2／754.9 ms，累計 CPU 時間為 1.297／21.547／62.828 秒。
這個案例增加執行緒僅省約 17 ms 首字時間，CPU 代價明顯，支持保留目前預設。
預覽與 CPU 定稿對合成來源的 49 個正規化字元均無替換、刪除或插入；定稿純運算
4.883 秒。這只說明受控合成音通過，不能代替真人口語、歌曲或中英切換的準確率。
