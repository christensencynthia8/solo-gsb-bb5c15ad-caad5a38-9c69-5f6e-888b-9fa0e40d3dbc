"""口述录音去静音服务：FastAPI + FFmpeg。"""

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .silence import (
    build_mapping,
    expand_and_merge,
    locate,
    parse_silences,
    speech_intervals,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_SUFFIXES = {".wav", ".mp3"}

app = FastAPI(title="口述录音去静音")


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe_audio(path: Path) -> dict:
    """用 ffprobe 读取声道数、采样率和时长。"""
    proc = _run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels:format=duration",
        "-of", "json", str(path),
    ])
    if proc.returncode != 0:
        raise HTTPException(400, f"无法解析音频文件：{proc.stderr.strip()[:300]}")
    info = json.loads(proc.stdout)
    if not info.get("streams"):
        raise HTTPException(400, "文件中没有音频流")
    stream = info["streams"][0]
    return {
        "sample_rate": int(stream["sample_rate"]),
        "channels": int(stream["channels"]),
        "duration": float(info["format"]["duration"]),
    }


def detect_silences(path: Path, threshold_db: float, min_silence: float, duration: float):
    """沿解码后的时间轴运行 silencedetect，返回静音区间列表。"""
    proc = _run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence}",
        "-f", "null", "-",
    ])
    return parse_silences(proc.stderr, duration)


def render_wav(src: Path, dst: Path, segments, sample_rate: int, channels: int):
    """用 atrim 裁出各保留段，concat 按原顺序拼接为 WAV，
    保持输入的声道数和采样率。"""
    parts = []
    labels = []
    for i, (s, e) in enumerate(segments):
        parts.append(
            f"[0:a]atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS[a{i}]"
        )
        labels.append(f"[a{i}]")
    parts.append("".join(labels) + f"concat=n={len(segments)}:v=0:a=1[out]")
    proc = _run([
        "ffmpeg", "-y", "-i", str(src),
        "-filter_complex", ";".join(parts),
        "-map", "[out]",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-c:a", "pcm_s16le",
        str(dst),
    ])
    if proc.returncode != 0:
        raise HTTPException(500, f"FFmpeg 处理失败：{proc.stderr.strip()[-300:]}")


def load_job(job_id: str) -> dict:
    path = DATA_DIR / job_id / "mapping.json"
    if not path.exists():
        raise HTTPException(404, "任务不存在或已过期")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/api/process")
async def process(
    file: UploadFile = File(...),
    threshold_db: float = Form(-35.0),
    min_silence: float = Form(0.5),
    padding_ms: int = Form(150),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, "仅支持 WAV / MP3 文件")
    if not (0.05 <= min_silence <= 60):
        raise HTTPException(400, "最短静音长度需在 0.05 ~ 60 秒之间")
    if not (0 <= padding_ms <= 5000):
        raise HTTPException(400, "保留毫秒需在 0 ~ 5000 之间")

    job_id = uuid.uuid4().hex[:12]
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(parents=True)
    src = job_dir / f"input{suffix}"
    try:
        with src.open("wb") as f:
            shutil.copyfileobj(file.file, f)

        meta = probe_audio(src)
        duration = meta["duration"]

        silences = detect_silences(src, threshold_db, min_silence, duration)
        speech = speech_intervals(silences, duration)
        segments = expand_and_merge(speech, padding_ms / 1000.0, duration)
        if not segments:
            raise HTTPException(422, "按当前参数未检测到任何语音段，请调低阈值或缩短最短静音长度")

        out = job_dir / "output.wav"
        render_wav(src, out, segments, meta["sample_rate"], meta["channels"])

        mapping, output_duration = build_mapping(segments)
        result = {
            "job_id": job_id,
            "original_duration": round(duration, 3),
            "output_duration": output_duration,
            "sample_rate": meta["sample_rate"],
            "channels": meta["channels"],
            "params": {
                "threshold_db": threshold_db,
                "min_silence": min_silence,
                "padding_ms": padding_ms,
            },
            "segments": mapping,
        }
        (job_dir / "mapping.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


@app.get("/api/jobs/{job_id}/audio")
def get_audio(job_id: str):
    path = DATA_DIR / job_id / "output.wav"
    if not path.exists():
        raise HTTPException(404, "音频不存在或已过期")
    return FileResponse(path, media_type="audio/wav", filename="output.wav")


@app.get("/api/jobs/{job_id}/mapping")
def get_mapping(job_id: str):
    path = DATA_DIR / job_id / "mapping.json"
    if not path.exists():
        raise HTTPException(404, "映射文件不存在或已过期")
    return FileResponse(path, media_type="application/json", filename="mapping.json")


@app.get("/api/jobs/{job_id}/query")
def query_time(job_id: str, t: float):
    """查询任意原始时间点，返回对应成品时间或标明已裁掉。"""
    job = load_job(job_id)
    if t < 0 or t > job["original_duration"]:
        raise HTTPException(400, f"时间点需在 0 ~ {job['original_duration']} 秒之间")
    removed, output_time = locate(job["segments"], t)
    return {"input_time": t, "removed": removed, "output_time": output_time}


app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
