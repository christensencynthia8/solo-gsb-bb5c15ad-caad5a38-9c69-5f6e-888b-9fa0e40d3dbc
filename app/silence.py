"""纯时间轴逻辑：静音区间解析、语音段扩展合并、原始->成品时间映射。

不依赖 FastAPI / FFmpeg，可单独测试。
"""

import re

SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def parse_silences(ffmpeg_stderr: str, duration: float):
    """从 ffmpeg silencedetect 的 stderr 解析静音区间 [(start, end), ...]。

    若文件以静音结尾（只有 silence_start 没有对应 silence_end），
    则把该静音段补到 duration。
    """
    silences = []
    open_start = None
    for line in ffmpeg_stderr.splitlines():
        m = SILENCE_START_RE.search(line)
        if m:
            open_start = max(0.0, float(m.group(1)))
            continue
        m = SILENCE_END_RE.search(line)
        if m and open_start is not None:
            silences.append((open_start, min(duration, float(m.group(1)))))
            open_start = None
    if open_start is not None:
        silences.append((open_start, duration))
    return silences


def speech_intervals(silences, duration: float):
    """静音区间沿解码时间轴取补集，得到语音区间。"""
    speech = []
    cursor = 0.0
    for s, e in sorted(silences):
        if s > cursor:
            speech.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        speech.append((cursor, duration))
    return speech


def expand_and_merge(speech, padding: float, duration: float):
    """每段语音前后各扩展 padding 秒作为保留边界，裁剪到 [0, duration]，
    再合并重叠或相接的区间。"""
    merged = []
    for s, e in speech:
        s = max(0.0, s - padding)
        e = min(duration, e + padding)
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def build_mapping(segments):
    """按原顺序拼接保留区间，计算每段在成品中的起止时间。

    返回 (mapping, output_duration)。
    """
    mapping = []
    cursor = 0.0
    for s, e in segments:
        mapping.append({
            "original_start": round(s, 3),
            "original_end": round(e, 3),
            "output_start": round(cursor, 3),
            "output_end": round(cursor + (e - s), 3),
        })
        cursor += e - s
    return mapping, round(cursor, 3)


def locate(mapping, t: float):
    """查询原始时间点 t。

    返回 (removed, output_time)：命中保留段时 removed=False 并给出成品时间，
    否则 removed=True、output_time=None（该点已被裁掉）。
    """
    for seg in mapping:
        if seg["original_start"] <= t <= seg["original_end"]:
            return False, round(seg["output_start"] + (t - seg["original_start"]), 3)
    return True, None
