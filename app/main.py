"""FastAPI app: upload audio, remove silence, serve result + time mapping."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import audio

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "200"))

app = FastAPI(title="Silence Cutter")

# job_id -> metadata dict (in-memory; files live under DATA_DIR/jobs/<id>/)
JOBS: dict[str, dict] = {}


def _job_dir(job_id: str) -> Path:
    return DATA_DIR / "jobs" / job_id


def _run_pipeline(path, threshold_db, min_silence_ms, padding_ms):
    """Decode -> detect -> cut. Blocking; meant for asyncio.to_thread."""
    sample_rate, channels = audio.probe(path)
    samples = audio.decode_pcm(path, sample_rate, channels)
    segments = audio.find_kept_segments(
        samples, sample_rate, channels,
        threshold_db, min_silence_ms, padding_ms,
    )
    output, mapping = audio.cut_and_map(samples, channels, segments)
    return sample_rate, channels, samples, segments, output, mapping


@app.post("/api/process")
async def process(
    file: UploadFile = File(...),
    threshold_db: float = Form(-40.0),
    min_silence_ms: float = Form(500.0),
    padding_ms: float = Form(150.0),
):
    if not (0 < min_silence_ms <= 600_000):
        raise HTTPException(400, "min_silence_ms must be in (0, 600000]")
    if not (0 <= padding_ms <= 60_000):
        raise HTTPException(400, "padding_ms must be in [0, 60000]")
    if not (-120 <= threshold_db <= 0):
        raise HTTPException(400, "threshold_db must be in [-120, 0]")

    suffix = Path(file.filename or "audio").suffix or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=DATA_DIR)
    size = 0
    try:
        with tmp:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1 << 20:
                    raise HTTPException(413, f"file exceeds {MAX_UPLOAD_MB} MB")
                tmp.write(chunk)

        # FFmpeg decode + NumPy analysis are blocking: run off the event loop.
        sample_rate, channels, samples, segments, output, mapping = (
            await asyncio.to_thread(_run_pipeline, tmp.name,
                                    threshold_db, min_silence_ms, padding_ms)
        )
    except audio.AudioError as exc:
        raise HTTPException(422, str(exc))
    finally:
        os.unlink(tmp.name)

    job_id = uuid.uuid4().hex[:12]
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    audio.write_wav(str(job_dir / "output.wav"), output, sample_rate, channels)

    total_in = len(samples) // channels
    seg_meta = [
        {
            "index": i + 1,
            "orig_start": m["orig"][0] / sample_rate,
            "orig_end": m["orig"][1] / sample_rate,
            "out_start": m["out"][0] / sample_rate,
            "out_end": m["out"][1] / sample_rate,
        }
        for i, m in enumerate(mapping)
    ]
    meta = {
        "job_id": job_id,
        "filename": file.filename,
        "sample_rate": sample_rate,
        "channels": channels,
        "params": {
            "threshold_db": threshold_db,
            "min_silence_ms": min_silence_ms,
            "padding_ms": padding_ms,
        },
        "original_duration": total_in / sample_rate,
        "output_duration": (len(output) // channels) / sample_rate,
        "segments": seg_meta,
    }
    mapping_doc = {
        "job_id": job_id,
        "sample_rate": sample_rate,
        "channels": channels,
        "original_duration": meta["original_duration"],
        "output_duration": meta["output_duration"],
        "segments": seg_meta,
    }
    (job_dir / "mapping.json").write_text(
        json.dumps(mapping_doc, indent=2, ensure_ascii=False)
    )
    JOBS[job_id] = {"meta": meta, "mapping": mapping}
    return meta


def _get_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.get("/api/jobs/{job_id}")
def job_meta(job_id: str):
    return _get_job(job_id)["meta"]


@app.get("/api/jobs/{job_id}/audio")
def job_audio(job_id: str):
    # Served inline so the <audio> player can stream it; the download link
    # uses the HTML `download` attribute to suggest a filename.
    _get_job(job_id)
    return FileResponse(_job_dir(job_id) / "output.wav", media_type="audio/wav")


@app.get("/api/jobs/{job_id}/mapping")
def job_mapping(job_id: str):
    _get_job(job_id)
    return FileResponse(
        _job_dir(job_id) / "mapping.json", media_type="application/json"
    )


@app.get("/api/jobs/{job_id}/query")
def job_query(job_id: str, t: float):
    job = _get_job(job_id)
    if t < 0 or t > job["meta"]["original_duration"]:
        raise HTTPException(400, "t is outside the original timeline")
    out = audio.query_mapping(
        job["mapping"], t, job["meta"]["sample_rate"]
    )
    if out is None:
        return {"input_time": t, "cut": True, "output_time": None}
    return {"input_time": t, "cut": False, "output_time": out}


static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
