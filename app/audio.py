"""Audio decoding, silence detection, cutting and WAV encoding.

FFmpeg is used only to decode arbitrary input (WAV/MP3/...) to float32 PCM.
Silence detection, interval math and cutting are done in Python/NumPy so the
sample-exact time mapping between the original and the output is known.
"""

from __future__ import annotations

import json
import subprocess
import wave

import numpy as np

FRAME_MS = 10  # analysis frame size


class AudioError(Exception):
    pass


def probe(path: str) -> tuple[int, int]:
    """Return (sample_rate, channels) of the first audio stream."""
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=sample_rate,channels",
                "-of", "json", path,
            ],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise AudioError("ffprobe not found; install FFmpeg")
    if proc.returncode != 0:
        raise AudioError(f"ffprobe failed: {proc.stderr.strip()}")
    streams = json.loads(proc.stdout or "{}").get("streams") or []
    if not streams:
        raise AudioError("no audio stream found in file")
    st = streams[0]
    return int(st["sample_rate"]), int(st["channels"])


def decode_pcm(path: str, sample_rate: int, channels: int) -> np.ndarray:
    """Decode to interleaved float32 PCM, keeping the original rate/channels."""
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-i", path,
                "-f", "f32le", "-acodec", "pcm_f32le",
                "-ar", str(sample_rate), "-ac", str(channels),
                "-",
            ],
            capture_output=True,
        )
    except FileNotFoundError:
        raise AudioError("ffmpeg not found; install FFmpeg")
    if proc.returncode != 0:
        raise AudioError(f"ffmpeg decode failed: {proc.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def merge_intervals(intervals: list[list[int]]) -> list[list[int]]:
    """Merge overlapping or touching [start, end) sample intervals."""
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def find_kept_segments(
    samples: np.ndarray,
    sample_rate: int,
    channels: int,
    threshold_db: float,
    min_silence_ms: float,
    padding_ms: float,
) -> list[list[int]]:
    """Return sample intervals of the original timeline to keep.

    Silence = frames whose RMS is below `threshold_db` for at least
    `min_silence_ms`. Speech segments are the complement, expanded by
    `padding_ms` on both sides, then merged where they overlap.
    """
    total = len(samples) // channels
    if total == 0:
        return []

    frame = max(1, int(sample_rate * FRAME_MS / 1000))
    n_frames = total // frame
    if n_frames == 0:
        # Shorter than one analysis frame: keep everything.
        return [[0, total]]

    framed = samples[: n_frames * frame * channels].reshape(n_frames, frame, channels)
    rms = np.sqrt(np.mean(framed.astype(np.float64) ** 2, axis=(1, 2)))
    db = 20.0 * np.log10(np.maximum(rms, 1e-10))
    silent = db < threshold_db

    min_silence_frames = max(1, int(round(min_silence_ms / FRAME_MS)))

    # Collect silence runs long enough to be cut.
    cut: list[list[int]] = []
    i = 0
    while i < n_frames:
        if silent[i]:
            j = i
            while j < n_frames and silent[j]:
                j += 1
            if j - i >= min_silence_frames:
                cut.append([i * frame, min(j * frame, total)])
            i = j
        else:
            i += 1

    # Kept speech = complement of the cut intervals.
    kept: list[list[int]] = []
    pos = 0
    for start, end in cut:
        if start > pos:
            kept.append([pos, start])
        pos = max(pos, end)
    if pos < total:
        kept.append([pos, total])

    # Expand speech boundaries and merge overlaps.
    pad = int(round(sample_rate * padding_ms / 1000))
    padded = [[max(0, s - pad), min(total, e + pad)] for s, e in kept]
    return merge_intervals(padded)


def cut_and_map(
    samples: np.ndarray,
    channels: int,
    segments: list[list[int]],
) -> tuple[np.ndarray, list[dict]]:
    """Concatenate kept segments in original order; build the time mapping."""
    out_parts = []
    mapping = []
    out_pos = 0
    for start, end in segments:
        out_parts.append(samples[start * channels: end * channels])
        length = end - start
        mapping.append(
            {"orig": [start, end], "out": [out_pos, out_pos + length]}
        )
        out_pos += length
    if out_parts:
        output = np.concatenate(out_parts)
    else:
        output = np.zeros(0, dtype=np.float32)
    return output, mapping


def write_wav(path: str, samples: np.ndarray, sample_rate: int, channels: int) -> None:
    """Write float32 PCM as a 16-bit WAV with the original rate/channels."""
    pcm16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def query_mapping(mapping: list[dict], t: float, sample_rate: int) -> float | None:
    """Map an original time (seconds) to the output time, or None if cut."""
    target = t * sample_rate
    for seg in mapping:
        os_, oe = seg["orig"]
        fs, fe = seg["out"]
        if os_ <= target < oe:
            return (fs + (target - os_)) / sample_rate
        # Boundary: exact end of the final segment maps to the output end.
        if target == oe and seg is mapping[-1]:
            return fe / sample_rate
    return None
