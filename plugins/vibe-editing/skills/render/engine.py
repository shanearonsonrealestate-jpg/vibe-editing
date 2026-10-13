#!/usr/bin/env python3
"""render — manifest-driven, stage-cached clip rendering engine.

Reads a project's manifest.json, runs the pipeline's stages in order, caches
each stage's output by content-hash so revisions only re-run what changed.

Usage:
    python3 engine.py <project_dir> [--from <stage>] [--bump]
"""
from __future__ import annotations

import argparse, hashlib, importlib.util, json, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
PIPELINES_DIR = SKILL_DIR / "pipelines"
STAGES_DIR = SKILL_DIR / "stages"


def sha256_file(p: Path, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b: break
            h.update(b)
    return h.hexdigest()[:16]


def hash_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def hash_path_or_value(v, base: Path) -> str:
    """If v is a string that resolves to an existing file under base or absolute, hash its content. Else hash the value."""
    if isinstance(v, str):
        p = Path(v)
        if not p.is_absolute(): p = base / v
        if p.exists() and p.is_file():
            return f"file:{sha256_file(p)}"
    return f"val:{hash_json(v)}"


def hash_config_with_refs(cfg, base: Path) -> str:
    """Hash a config dict; any string value that points at an existing file is hashed by content (so editing the file invalidates the cache)."""
    out = {}
    for k, v in (cfg or {}).items():
        if isinstance(v, dict):
            out[k] = hash_config_with_refs(v, base)
        elif isinstance(v, list):
            out[k] = [hash_path_or_value(x, base) for x in v]
        else:
            out[k] = hash_path_or_value(v, base)
    return hash_json(out)


def load_stage(name: str):
    p = STAGES_DIR / f"{name}.py"
    if not p.exists():
        raise FileNotFoundError(f"No stage module: {p}")
    # Ensure stages dir is on sys.path so `from _util import ...` works inside a stage.
    if str(STAGES_DIR) not in sys.path:
        sys.path.insert(0, str(STAGES_DIR))
    spec = importlib.util.spec_from_file_location(f"render.stages.{name}", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _valid_mp4(p: Path) -> bool:
    """A cached stage output is only a real HIT if it's a readable mp4 with a real duration.
    A render that died on out-of-space (or any crash) leaves a partial/corrupt file that EXISTS —
    serving it as a cache HIT ships/blocks a broken clip (StayInAGreatMood 2026-06-11). Probe it."""
    try:
        if not p.exists() or p.stat().st_size < 10_000:
            return False
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=30)
        return r.returncode == 0 and float((r.stdout or "0").strip() or 0) > 0.05
    except Exception:
        return False


def _ffprobe_observed(p: Path) -> dict:
    """Probe the delivered file for observed truth (what actually got rendered)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
             "-show_streams", str(p)],
            capture_output=True, text=True, timeout=30,
        )
        j = json.loads(r.stdout or "{}")
        fmt = j.get("format", {}) or {}
        vstream = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
        astream = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), {})
        fps_raw = vstream.get("r_frame_rate", "0/1")
        try:
            num, den = fps_raw.split("/")
            fps = round(float(num) / float(den), 3) if float(den) else None
        except Exception:
            fps = None
        return {
            "size_bytes": int(fmt.get("size") or 0) or p.stat().st_size,
            "duration_seconds": round(float(fmt.get("duration") or 0), 3),
            "width": vstream.get("width"),
            "height": vstream.get("height"),
            "vcodec": vstream.get("codec_name"),
            "acodec": astream.get("codec_name"),
            "fps": fps,
            "aspect": f"{vstream.get('width')}:{vstream.get('height')}" if vstream.get("width") else None,
        }
    except Exception as e:
        return {"error": str(e)}


def emit_contract(project: Path, manifest: dict, delivered: Path, contract_path: Path) -> None:
    """Emit clip.contract.json next to the delivered .mp4.

    Declared truth (from manifest + source files) + observed truth (from ffprobe on the
    rendered file) + audit hints. Each audit agent reads this FIRST and flags any
    mismatch between contract and observed reality as a FAIL — so audits become
    deterministic instead of pixel-guessing.
    """
    stages = manifest.get("stages", {}) or {}
    cuts_spec = stages.get("cut", {}).get("spec")
    ass_path = stages.get("captions", {}).get("ass")
    music_path = stages.get("mix", {}).get("music") or ""
    pipeline = manifest.get("pipeline", "unknown")

    # Count segments from cuts.json
    n_segments = None
    first_label = last_label = None
    if cuts_spec:
        cp = (project / cuts_spec) if not Path(cuts_spec).is_absolute() else Path(cuts_spec)
        if cp.exists():
            try:
                cuts = json.loads(cp.read_text()).get("segments") or []
                n_segments = len(cuts)
                if cuts:
                    first_label = cuts[0].get("label")
                    last_label = cuts[-1].get("label")
            except Exception:
                pass

    # Count caption events (ASS dialogue lines)
    n_caption_events = None
    if ass_path:
        ap = (project / ass_path) if not Path(ass_path).is_absolute() else Path(ass_path)
        if ap.exists():
            try:
                n_caption_events = sum(1 for ln in ap.read_text(errors="ignore").splitlines()
                                       if ln.startswith("Dialogue:"))
            except Exception:
                pass

    # Speaker setup → caption color map (context may be a free-text string or a dict)
    _ctx = stages.get("captions", {}).get("context") or {}
    speakers = ((_ctx.get("speakers") if isinstance(_ctx, dict) else None)
                or (2 if pipeline in ("qa", "podcast") else 1))
    if int(speakers) == 2:
        color_map = {"host": "white", "guest": "yellow"}
    else:
        color_map = {"speaker": "white"}

    contract = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "pipeline": pipeline,
            "project_dir": str(project),
            "manifest": "manifest.json",
        },
        "declared": {
            "title": manifest.get("title"),
            "output_name": manifest.get("output", {}).get("name"),
            "reframe_preset": stages.get("reframe", {}).get("preset"),
            "reframe_zoom": stages.get("reframe", {}).get("zoom"),
            "reframe_res": stages.get("reframe", {}).get("res"),
            "captions": {
                "style": "spice",
                "ass": ass_path,
                "context": stages.get("captions", {}).get("context") or {},
                "n_events": n_caption_events,
                "color_map": color_map,
            },
            "music": {
                "path": music_path,
                "name": Path(music_path).name if music_path else None,
                "voice_lufs": stages.get("mix", {}).get("voice_lufs"),
                "music_lufs": stages.get("mix", {}).get("music_lufs"),
            },
            "cut": {
                "n_segments": n_segments,
                "first_segment_label": first_label,
                "last_segment_label": last_label,
                "hard_end": True,
            },
            "speakers": int(speakers),
        },
        "observed": {
            "delivered_path": str(delivered),
            **_ffprobe_observed(delivered),
        },
        "audit_hints": {
            "expected_aspect_wh": [1080, 1920],
            "max_duration_seconds": 90,
            "min_duration_seconds": 8,
            "expected_caption_style": "spice",
            "no_fade_out": True,
            "delivery_target": "SPEAKER_FRAME" if pipeline in ("qa", "listicle", "single") else "LOCAL_ONLY",
        },
    }

    contract_path.write_text(json.dumps(contract, indent=2))


def next_version_dir(deliver_root: Path, bump: bool) -> Path:
    """Find v<N> dir. If bump, create v<N+1>. Else return latest existing or v1."""
    deliver_root.mkdir(parents=True, exist_ok=True)
    existing = sorted(int(d.name[1:]) for d in deliver_root.iterdir()
                      if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit())
    n = (max(existing) + 1) if bump and existing else (max(existing) if existing else 1)
    d = deliver_root / f"v{n}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path, help="Project root containing manifest.json")
    ap.add_argument("--from", dest="from_stage", default=None, help="Force re-run starting at this stage (skip cache from here forward)")
    ap.add_argument("--bump", action="store_true", help="Deliver to v<N+1> instead of latest v<N>")
    ap.add_argument("--dry-run", action="store_true", help="Print stage plan + cache hits/misses; don't execute")
    a = ap.parse_args()

    project = a.project.resolve()
    manifest_path = project / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"ERROR: no manifest.json in {project}")

    manifest = json.loads(manifest_path.read_text())
    title = manifest["title"]
    pipeline_name = manifest["pipeline"]
    pipeline_path = PIPELINES_DIR / f"{pipeline_name}.json"
    if not pipeline_path.exists():
        sys.exit(f"ERROR: no pipeline {pipeline_name} ({pipeline_path})")
    pipeline = json.loads(pipeline_path.read_text())
    stages_order = pipeline["stages"]

    work = project / "10_WORK"
    work.mkdir(exist_ok=True)
    stages_cache = work / "stages"
    stages_cache.mkdir(exist_ok=True)

    print(f"[render] project={project.name} pipeline={pipeline_name} stages={'→'.join(stages_order)}")

    # Resolve "from" — every stage at this index or later runs uncached.
    force_from = stages_order.index(a.from_stage) if a.from_stage else None

    upstream_hash = "ROOT"  # accumulator: chains stages so editing stage N invalidates N+1...
    prior_outputs = {}      # {stage_name: path/to/cached/output.mp4}
    prior_meta = {}         # {stage_name: stage's returned meta dict}
    last_stage_name = None

    for idx, stage_name in enumerate(stages_order):
        stage = load_stage(stage_name)
        cfg = manifest.get("stages", {}).get(stage_name, {})

        # Cache key = stage version + this stage's config (with file-content hashing) + upstream hash
        key_components = {
            "stage": stage_name,
            "version": getattr(stage, "VERSION", "0"),
            "config": hash_config_with_refs(cfg, project),
            "upstream": upstream_hash,
        }
        cache_key = hash_json(key_components)
        cache_dir = stages_cache / stage_name
        cache_dir.mkdir(exist_ok=True)
        cached_out = cache_dir / f"{cache_key}.mp4"
        cached_meta = cache_dir / f"{cache_key}.meta.json"

        forced = force_from is not None and idx >= force_from
        hit = cached_out.exists() and not forced
        if hit and not _valid_mp4(cached_out):
            print(f"      ⚠ cached output corrupt/partial (crash or out-of-space) — discarding, re-rendering")
            cached_out.unlink(missing_ok=True)
            hit = False

        print(f"  [{idx+1}/{len(stages_order)}] {stage_name:<10} v{getattr(stage,'VERSION','0'):<6} "
              f"key={cache_key} {'HIT' if hit else ('MISS' if not forced else 'FORCED')}")

        if a.dry_run:
            prior_outputs[stage_name] = str(cached_out)
            upstream_hash = cache_key
            last_stage_name = stage_name
            continue

        if not hit:
            t0 = time.time()
            result = stage.run(work_dir=work, config=cfg, inputs=dict(prior_outputs),
                               inputs_meta=dict(prior_meta),
                               project=project, manifest=manifest, out_path=cached_out)
            elapsed = time.time() - t0
            if "out" not in result or not Path(result["out"]).exists():
                sys.exit(f"ERROR: stage {stage_name} did not produce {cached_out}")
            stage_meta = result.get("meta", {}) or {}
            cached_meta.write_text(json.dumps({
                "stage": stage_name,
                "version": getattr(stage, "VERSION", "0"),
                "cache_key": cache_key,
                "key_components": key_components,
                "elapsed_s": round(elapsed, 2),
                "config": cfg,
                "meta": stage_meta,
            }, indent=2, default=str))
            print(f"           → {cached_out.name} ({elapsed:.1f}s)")
            prior_meta[stage_name] = stage_meta
        else:
            # On cache hit, load the stage's saved meta from .meta.json so downstream gets it
            if cached_meta.exists():
                try:
                    prior_meta[stage_name] = json.loads(cached_meta.read_text()).get("meta", {}) or {}
                except Exception:
                    prior_meta[stage_name] = {}
            else:
                prior_meta[stage_name] = {}

        prior_outputs[stage_name] = str(cached_out)
        upstream_hash = cache_key
        last_stage_name = stage_name

    if a.dry_run:
        print("[dry-run] no changes written")
        return 0

    # Deliver. Two modes:
    #   versioned (default): 20_DELIVER/v<N>/<name>  — keeps a history of renders
    #   flat (output.versioned:false): 20_DELIVER/<name> — OVERWRITES in place, no v<N> bloat
    out_cfg = manifest.get("output", {})
    deliver_root = project / out_cfg.get("dir", "20_DELIVER")
    if not deliver_root.is_absolute():
        deliver_root = project / deliver_root
    out_name = manifest["output"]["name"]
    if out_cfg.get("versioned", True) is False:
        deliver_root.mkdir(parents=True, exist_ok=True)
        deliver_dir = deliver_root
    else:
        deliver_dir = next_version_dir(deliver_root, a.bump)
    deliver_path = deliver_dir / out_name
    shutil.copy2(prior_outputs[last_stage_name], deliver_path)
    print(f"[render] DELIVER → {deliver_path}")

    # Emit clip.contract.json — declared (manifest) + observed (ffprobe) truth side-by-side.
    # Audit agents read this FIRST and flag any mismatch as a FAIL (deterministic auditing
    # instead of pixel-guessing). Non-fatal on error — the .mp4 is still delivered.
    try:
        # Namespace the contract by the output stem so multiple clips can share ONE
        # flat delivery folder without their contracts colliding.
        contract_path = deliver_dir / (Path(out_name).stem + ".contract.json")
        emit_contract(project, manifest, deliver_path, contract_path)
        print(f"[render] CONTRACT → {contract_path.name}")
    except Exception as e:
        print(f"[render] WARN contract emit failed: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
