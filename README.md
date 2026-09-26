# 口述录音去静音（Silence Cutter）

上传 WAV / MP3，按可调阈值自动检测并裁掉静音段，把语音段按原顺序拼接为 WAV 成品。
成品保持输入的**声道数与采样率**，并提供原始 → 成品的精确时间映射。

技术栈：Python FastAPI + FFmpeg（解码）+ NumPy（静音检测/裁剪）+ 原生 JavaScript。

## 一键启动

```bash
docker compose up --build
```

然后访问：**http://localhost:8000**

（镜像内已安装 FFmpeg 与全部 Python 依赖，无需本机预装。）

## 使用

1. 打开页面，选择 WAV 或 MP3 文件。
2. 设置三个参数（见下），点击「开始处理」。
3. 页面显示原始/成品时长、每个保留段的原始与成品起止时间，
   可在线试听原始与成品、下载成品 WAV 和 JSON 时间映射。
4. 在「时间点查询」输入任意原始时间点（秒），返回对应成品时间，或标明「已裁掉」。

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| 静音阈值 (dB) | -40 | 帧 RMS 低于该值视为静音 |
| 最短静音长度 (ms) | 500 | 静音持续超过该长度才会被裁掉 |
| 语音段前后保留 (ms) | 150 | 每段语音首尾额外保留的边界，避免切掉气息/尾音 |

处理流程：FFmpeg 解码为 PCM → 10ms 帧计算 RMS → 找出超长静音段 →
语音段向两侧扩展保留边界并合并重叠区间 → 裁掉其余部分按原顺序拼接 → 输出 WAV。

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATA_DIR` | `/data`（容器）/ `./data`（本地） | 任务产物存储目录 |
| `MAX_UPLOAD_MB` | `200` | 上传大小上限（MB） |

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/process` | 表单上传：`file`、`threshold_db`、`min_silence_ms`、`padding_ms`，返回任务元数据 |
| `GET` | `/api/jobs/{id}` | 任务元数据（时长、分段明细） |
| `GET` | `/api/jobs/{id}/audio` | 下载/试听成品 WAV |
| `GET` | `/api/jobs/{id}/mapping` | 下载 JSON 时间映射 |
| `GET` | `/api/jobs/{id}/query?t=秒` | 查询原始时间点 → `{output_time}` 或 `{cut: true}` |

示例：

```bash
curl -F "file=@voice.mp3" -F "threshold_db=-40" \
     -F "min_silence_ms=500" -F "padding_ms=150" \
     http://localhost:8000/api/process

curl "http://localhost:8000/api/jobs/<job_id>/query?t=12.5"
# {"input_time":12.5,"cut":false,"output_time":9.875}
```

## 本地开发（不用 Docker）

```bash
# 需要本机已安装 ffmpeg / ffprobe
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
