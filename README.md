# 口述录音去静音

上传 WAV/MP3，自动检测并裁掉静音段，把语音段按原顺序拼接为 WAV。页面可试听、下载成品音频与 JSON 时间映射，并支持查询任意原始时间点在成品中的位置（或标明已裁掉）。

技术栈：Python FastAPI + FFmpeg（silencedetect / atrim / concat）+ 原生 JavaScript。

## 一键启动

```bash
docker compose up --build
```

启动后访问：**http://localhost:8000**

## 使用说明

1. 选择 WAV 或 MP3 文件；
2. 设置参数：
   - **静音阈值（dB）**：低于该音量视为静音，默认 `-35`，常用范围 -30 ~ -45；
   - **最短静音长度（秒）**：超过该时长的静音才会被裁掉，默认 `0.5`；
   - **语音段前后保留（毫秒）**：每段语音前后各保留的边界，避免切掉气口和尾音，默认 `150`；
3. 点击「开始处理」，页面显示：
   - 原始时长 / 成品时长 / 裁掉比例 / 保留段数 / 采样率与声道数；
   - 原始与成品音频试听；
   - 每段的原始起止时间与成品起止时间对照表；
   - 下载成品 WAV 与时间映射 JSON；
   - 输入任意原始时间点（秒），查询其对应成品时间或「已裁掉」。

成品 WAV 保持输入文件的**声道数和采样率**（PCM 16-bit）。

## 处理流程

1. `ffprobe` 读取声道数、采样率、时长；
2. `ffmpeg -af silencedetect` 沿解码后的时间轴找出静音段；
3. 取补集得到语音段，前后各扩展保留毫秒，合并重叠/相接区间；
4. `atrim` + `concat` 裁掉其余部分，按原顺序拼接输出 WAV；
5. 同步生成原始时间轴 → 成品时间轴的映射 JSON。

## 配置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| 端口 | `8000` | 在 `docker-compose.yml` 的 `ports` 中修改，如 `"8080:8000"` |
| `DATA_DIR` | `/data`（容器内） | 处理结果（音频 + mapping.json）存放目录，Compose 已挂载命名卷 `silence-data` |

## 本地开发（不用 Docker）

```bash
# 需先安装 FFmpeg 与 Python 3.11+
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/process` | 上传音频（表单字段：`file`、`threshold_db`、`min_silence`、`padding_ms`），返回分段与映射 |
| `GET` | `/api/jobs/{job_id}/audio` | 下载成品 WAV |
| `GET` | `/api/jobs/{job_id}/mapping` | 下载时间映射 JSON |
| `GET` | `/api/jobs/{job_id}/query?t=秒` | 查询原始时间点 → 成品时间或 `removed: true` |
