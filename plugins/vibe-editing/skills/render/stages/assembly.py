"""assembly — multicam Q&A / interview: build the vertical cut from an EDL with edit/scripts/qa_assembly.py.

Replaces cut + reframe + grade for footage where host and guest each have their OWN camera and
mic. qa_assembly cuts between angles per the EDL (speaker / guest / split / wide), face-tracks
each angle, stacks host-over-guest for "split" shots, grades per camera, and mixes both mics
(word-snapped seams, -14 LUFS). This stage runs it WITHOUT captions or music — the captions and
mix stages add those, so caption fixes and music swaps stay surgical (no re-tracking).

Config (manifest.stages.assembly):
    {
      "edl":  "10_WORK/edl.json",     # {"segments":[{"cam","mic_start","mic_end","speaker"}, ...]}
      "sync": "10_WORK/qa_sync.json", # cam_dir, per-camera offsets + roles, mics, speaker_mics,
                                      # optional guest_split {...} — see edit/references/qa_sync.json
      "format": "qa",                 # qa | hotline | dtc
      "res": "1080"                   # "1080" or "4k" (VIBE_QA_RES)
    }
Paths in the sync file (cam_dir, mics) resolve relative to the project dir.

Meta for downstream stages: "segments" (frame ranges, like the cut stage), "split_segments"
(indices shown as split-screen — captions ride the seam there), and "speaker_windows" +
"speaker_mics" so the captions stage colors host white / guest yellow from each person's mic.
"""
from __future__ import annotations

# ── vibe-editing portable path bootstrap (auto-inserted) ──
import os as _os, sys as _sys
def _acq_root():
    r = _os.environ.get("VIBE_PIPELINE_ROOT") or _os.environ.get("CLAUDE_PLUGIN_ROOT")
    if r and _os.path.isdir(_os.path.join(r, ".claude-plugin")):
        return r
    d = _os.path.dirname(_os.path.abspath(__file__))
    while d != _os.path.dirname(d):
        if _os.path.isdir(_os.path.join(d, ".claude-plugin")):
            return d
        d = _os.path.dirname(d)
    return _os.path.dirname(_os.path.abspath(__file__))
VIBE_ROOT = _acq_root()
VIBE_SHARED = _os.path.join(VIBE_ROOT, "lib", "_shared")
VIBE_SKILLS = _os.path.join(VIBE_ROOT, "skills")
def _acq(p):
    import pathlib as _pl
    parts = [x for x in str(p).strip("/").split("/") if x]
    if parts and parts[0] == "_shared":
        return _pl.Path(_os.path.join(VIBE_ROOT, "lib", *parts))
    return _pl.Path(_os.path.join(VIBE_SKILLS, *parts))
if VIBE_SHARED not in _sys.path:
    _sys.path.insert(0, VIBE_SHARED)
# ── end bootstrap ──
import hashlib
import json
import os
import subprocess
from pathlib import Path

from _util import resolve_path

QA_ASSEMBLY = _acq("edit/scripts/qa_assembly.py")
_DEPS = [QA_ASSEMBLY,
         _acq("horizontal-to-vertical/scripts/qa_reframe_v2.py"),
         _acq("horizontal-to-vertical/scripts/guest_panel_render.py")]


def _dep_hash() -> str:
    h = hashlib.sha256()
    for p in _DEPS:
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"missing:" + str(p).encode())
    return h.hexdigest()[:8]


VERSION = "1.0.0+" + _dep_hash()


def _fps(rate: str) -> float:
    if "/" in str(rate):
        n, d = str(rate).split("/")
        return int(n) / int(d)
    return float(rate)


def _count_frames(p) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                          "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True)
    return int(out.stdout.strip())


def run(work_dir, config, inputs, inputs_meta, project, manifest, out_path):
    edl = resolve_path(config["edl"], project)
    sync = resolve_path(config["sync"], project)
    for what, p in (("edl", edl), ("sync", sync)):
        if not Path(p).exists():
            raise FileNotFoundError(f"assembly.{what} not found: {p}")
    map_path = Path(work_dir) / f"{Path(out_path).stem}_map.json"
    res = str(config.get("res", "1080"))
    env = dict(os.environ, VIBE_QA_RES=("2160" if res.lower() in ("4k", "2160") else "1080"))

    cmd = ["python3", str(QA_ASSEMBLY), "--edl", str(edl), "--sync", str(sync),
           "--out", str(out_path), "--format", str(config.get("format", "qa")),
           "--no-captions", "--map-out", str(map_path)]
    # cwd=project: the sync file's cam_dir / mic paths are project-relative
    r = subprocess.run(cmd, cwd=str(project), env=env)
    if r.returncode != 0:
        raise SystemExit(f"assembly stage FAILED (qa_assembly exit {r.returncode}) — render aborted")
    if not Path(out_path).exists():
        raise SystemExit(f"assembly: qa_assembly reported success but {out_path} is missing")

    m = json.loads(map_path.read_text())
    fps = _fps(m.get("fps", "30000/1001"))
    rows = m["segments"]
    total_f = _count_frames(out_path)
    segments, split_idx = [], []
    for i, row in enumerate(rows):
        in_f = int(round(row["clip_start"] * fps))
        out_f = total_f if i == len(rows) - 1 else int(round(row["clip_end"] * fps))
        segments.append({"in_frame": in_f, "out_frame": out_f,
                         "duration_s": round(row["clip_end"] - row["clip_start"], 3)})
        if row["cam"] == "split":
            split_idx.append(i)

    def _abs(p):
        return str(p if Path(p).is_absolute() else Path(project) / p)

    return {"out": str(out_path), "meta": {
        "fps": fps,
        "total_duration_s": round(rows[-1]["clip_end"], 3) if rows else 0.0,
        "segments": segments,
        "split_segments": split_idx,
        # EDL speaker per segment (host = the EDL's "speaker" role) + where each segment sits on the mics
        "speaker_windows": [[r["clip_start"], r["clip_end"], r["mic_start"],
                             "guest" if r.get("speaker") == "guest" else "host"] for r in rows],
        "speaker_mics": {("host" if k == "speaker" else k): _abs(v) for k, v in m.get("speaker_mics", {}).items()},
        "speaker_color_from_edl": bool(m.get("speaker_color_from_edl")),
    }}
