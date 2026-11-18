"""Verbatim cue records and conservative capture coverage."""
import math
import re
from decimal import Decimal

STAMP = r"(?:\d+:)?[0-5]?\d:[0-5]\d(?:[.,]\d+)?"
TIMESTAMP = re.compile(STAMP + r"\Z")
TXT_ROW = re.compile(r"^\[(" + STAMP + r")\] ", re.MULTILINE)


def seconds(stamp):
    if not TIMESTAMP.fullmatch(stamp):
        raise ValueError("Unsupported caption timestamp")
    total = Decimal(0)
    for part in stamp.replace(",", ".").split(":"):
        total = total * 60 + Decimal(part)
    return total


def import_txt(text):
    """Read the bracketed timestamp format, including previous chat exports."""
    matches = list(TXT_ROW.finditer(text))
    if not matches:
        raise ValueError("No bracketed caption timestamps found")
    cues = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        wording = text[match.end():end].removesuffix("\n")
        cues.append({"timestamp": match[1], "text": wording})
    return cues


def transcript(cues):
    return "".join(f"[{cue['timestamp']}] {cue['text']}\n" for cue in cues)


def merge_snapshot(previous, current):
    if current[:len(previous)] != previous:
        raise ValueError("Caption list changed, reordered or virtualized during scrolling")
    return current


def assess(cues, runtime, stable_end, expected_count=None, download_options=(), *,
           edge_seconds=8, gap_seconds=10):
    starts = [seconds(cue["timestamp"]) for cue in cues]
    if any(not isinstance(cue["text"], str) for cue in cues):
        raise ValueError("Each cue requires source text")
    if any(a > b for a, b in zip(starts, starts[1:])):
        raise ValueError("Caption times are out of order")
    runtime = float(runtime) if runtime is not None else None
    if runtime is not None and (not math.isfinite(runtime) or runtime <= 0):
        raise ValueError("Invalid video runtime")
    uncaptioned_tail = max(0.0, runtime - float(starts[-1])) if starts and runtime else None
    gaps = [
        {"from": cues[i]["timestamp"], "to": cues[i + 1]["timestamp"], "seconds": float(b - a)}
        for i, (a, b) in enumerate(zip(starts, starts[1:])) if b - a > gap_seconds
    ]
    reasons = []
    if not stable_end:
        reasons.append("Caption list end was not verified")
    if runtime is None:
        reasons.append("Player runtime was unavailable")
    if starts and starts[0] > edge_seconds:
        reasons.append("First cue starts well after the video begins")
    if uncaptioned_tail is not None and uncaptioned_tail > edge_seconds:
        reasons.append("Last cue starts well before the video ends")
    if starts and runtime is not None and float(starts[-1]) > runtime + 1:
        reasons.append("Last cue exceeds player runtime")
    if expected_count is not None and expected_count != len(cues):
        reasons.append("Cue count differs from the supplied reference count")
    if gaps:
        reasons.append("Long cue-start intervals require review")
    status = "unavailable" if not cues else ("partial" if reasons else "full")
    indices = sorted({0, len(cues) // 2, len(cues) - 1}) if cues else []
    excerpts = [
        {"cue": i + 1, "timestamp": cues[i]["timestamp"], "text": cues[i]["text"][:180]}
        for i in indices
    ]
    return {
        "status": status, "cue_count": len(cues), "runtime_seconds": runtime,
        "first": cues[0]["timestamp"] if cues else None,
        "last": cues[-1]["timestamp"] if cues else None,
        "uncaptioned_tail_seconds": uncaptioned_tail,
        "cue_start_gaps": gaps, "stable_list_end": bool(stable_end),
        "reasons": reasons, "excerpts": excerpts,
        "visible_download_options": list(download_options),
        "scope": "Displayed caption cues. Start-to-start intervals can include speech or silence.",
        "edge_tolerance_seconds": edge_seconds, "gap_threshold_seconds": gap_seconds,
    }
