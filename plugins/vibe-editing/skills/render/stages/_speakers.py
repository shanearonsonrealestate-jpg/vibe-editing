"""_speakers — per-word host/guest caption color from each person's own mic (podcast pipeline).

The caption director guesses who is talking from the transcript text alone, which drifts on a
two-person podcast where both people talk at length. When each person has their own mic track,
the louder mic during a word is ground truth. This module reads both mics over each caption word
and writes the answer into the director stream as an explicit per-word color:
    host  -> "c": "speaker" (white)
    guest -> "c": "guest"   (yellow)

Same decision rule as edit/scripts/speaker_diarize.py (imported, so the thresholds and the
turn-reply word list stay identical everywhere):
  1. a mic wins by >= CONF dB          -> that person
  2. a short reply token ("yeah", ...) -> the OTHER person from the previous word (turn-taking)
  3. a mic leans by >= SOFT dB         -> that person
  4. otherwise                         -> same person as the previous word (continuity)

Mic times: each mic file is on the SAME timeline as the cut stage's source (the times in
cuts.json, after the cut stage's silence snap), shifted by an optional offset_s (mic_time = source_time + offset_s). A cut segment
that names its own source_audio/source_video is from a different recording, so its words keep
the director's color and a warning is printed.

Reported speech: when the host quotes someone ("and he says 'just raise the price'"), the
director marks those words q=true + guest. Mic truth says host, but the quote stays yellow — that
is the house caption rule (caption_director.py), not a diarization error.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

_EDIT_SCRIPTS = Path(__file__).resolve().parents[2] / "edit" / "scripts"
if str(_EDIT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_EDIT_SCRIPTS))
from speaker_diarize import CONF, SOFT, RESPONSE, _bare  # noqa: E402

SR = 16000


def _decode(path: Path, t0: float, dur: float) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t0):.3f}", "-t", f"{dur:.3f}", "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype=np.float32)
    if t0 < 0:  # mic starts after this point on the source timeline: pad the gap with silence
        a = np.concatenate([np.zeros(int(round(-t0 * SR)), dtype=np.float32), a])
    return a


def _db(a: np.ndarray, s0: int, s1: int) -> float:
    seg = a[max(0, s0):max(0, s1)]
    if seg.size == 0:
        return -120.0
    return float(10 * np.log10(np.mean(seg.astype(np.float64) ** 2) + 1e-12))


def decide(words, host_db, guest_db):
    """Pure decision rule. words: list of token strings; host_db/guest_db: per-word levels
    (None = no mic data for that word). Returns a list of "host" | "guest" | None."""
    out, prev = [], None
    for tok, h, g in zip(words, host_db, guest_db):
        if h is None or g is None:
            out.append(None)
            continue
        d = h - g
        if abs(d) >= CONF:
            who = "host" if d > 0 else "guest"
        elif _bare(tok) in RESPONSE and prev is not None:
            who = "guest" if prev == "host" else "host"
        elif abs(d) >= SOFT:
            who = "host" if d > 0 else "guest"
        else:
            who = prev or ("host" if d >= 0 else "guest")
        out.append(who)
        prev = who
    return out


def apply_mic_colors(spice_norm: Path, stream_path: Path, cut_spec: dict, cut_segments: list,
                     host_mic: Path, guest_mic: Path, offset_s: float = 0.0) -> dict:
    """Overwrite per-word "c" in the director stream from mic energy. Returns a small report."""
    words = json.loads(Path(spice_norm).read_text())["words"]
    stream = json.loads(Path(stream_path).read_text()) if Path(stream_path).exists() else {}
    stream.setdefault("words", {})
    stream.setdefault("voice_spans", [])
    spec_segs = cut_spec.get("segments") or []
    if len(spec_segs) != len(cut_segments):
        raise RuntimeError(f"speaker mics: cuts.json has {len(spec_segs)} segments but the cut stage "
                           f"reported {len(cut_segments)} — can't map clip time to mic time")

    # clip-time window of each segment, from the cut stage's own measured durations
    windows, t = [], 0.0
    for spec, meta in zip(spec_segs, cut_segments):
        d = float(meta["duration_s"])
        # src_in = the cut stage's SNAPPED in-point (it nudges boundaries to silence); older cut
        # caches lack it, so fall back to the raw cuts.json "in".
        src_in = float(meta.get("src_in", spec["in"]))
        windows.append((t, t + d, src_in, bool(spec.get("source_audio") or spec.get("source_video"))))
        t += d

    host_db, guest_db, skipped = [], [], 0
    cache = {}
    for w in words:
        mid = 0.5 * (float(w["start"]) + float(w["end"]))
        seg = next((i for i, (c0, c1, _, _) in enumerate(windows) if c0 <= mid < c1), None)
        if seg is None and windows:  # ASR end times can overshoot the clip end by a few ms
            seg = 0 if mid < windows[0][0] else len(windows) - 1
        if seg is None or windows[seg][3]:
            host_db.append(None); guest_db.append(None); skipped += 1
            continue
        c0, c1, src_in, _ = windows[seg]
        if seg not in cache:
            m0 = src_in + offset_s
            cache[seg] = (_decode(host_mic, m0, c1 - c0), _decode(guest_mic, m0, c1 - c0))
        ha, ga = cache[seg]
        s0 = int((float(w["start"]) - c0) * SR)
        s1 = int((float(w["end"]) - c0) * SR)
        if s1 <= s0:
            s1 = s0 + int(0.05 * SR)
        host_db.append(_db(ha, s0, s1))
        guest_db.append(_db(ga, s0, s1))

    who = decide([w.get("word", "") for w in words], host_db, guest_db)
    n_host = n_guest = kept_quotes = 0
    for i, p in enumerate(who):
        if p is None:
            continue
        d = stream["words"].setdefault(str(i), {})
        if p == "guest":
            d["c"] = "guest"; n_guest += 1
        elif d.get("q"):
            kept_quotes += 1  # host quoting someone: stays guest-colored (house rule)
        else:
            d["c"] = "speaker"; n_host += 1
    Path(stream_path).write_text(json.dumps(stream, indent=1))
    if skipped:
        print(f"  ⚠️ speaker mics: {skipped} word(s) in segments with their own source kept the "
              f"director's color (mics only cover the stage-level source)", flush=True)
    return {"host_words": n_host, "guest_words": n_guest, "quotes_kept": kept_quotes,
            "unmapped_words": skipped}
