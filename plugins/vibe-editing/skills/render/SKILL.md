---
name: render
description: THE manifest-driven, stage-cached rendering engine. Every clip is a recipe (`manifest.json` + source files for each layer — `cuts.json`, `reframe.json`, `captions.ass`, `music.json`); render builds it in ordered stages (`cut → reframe → grade → captions → mix → leadfix → deliver`); each stage's output is content-hashed into `10_WORK/stages/<stage>/<hash>.mp4`. Edit any source file and re-run — the engine detects which stage's inputs changed and rebuilds from THAT stage forward, cache-hitting everything above it. **The whole point: revisions on a delivered clip become surgical.** Caption typo → edit `captions.ass`, re-run → only the captions+mix+leadfix+deliver stages re-execute (≈5s for a 50s listicle). Cut adjustment → edit `cuts.json`, re-run → everything re-renders from cut down. **One pipeline per clip type** (`pipelines/listicle.json`, `pipelines/qa.json`, etc.) defines the stage chain; project `manifest.json` references a pipeline and supplies its config. Use whenever building or revising a clip — never hand-call individual stages, the engine is the entry point. Trigger keywords render, re-render, revise clip, apply revision, caption fix, recut, rebuild, stage cache, manifest, layered render, surgical revision.
---

# render — manifest-driven, stage-cached clip rendering

**The problem this solves:** delivered .mp4s are flat — all the layers (cuts, reframe, grade, captions, music) baked into one file. Any revision = either re-render from scratch (which can change unrelated things) or overlay hack (which is fragile). This skill makes revisions **surgical** by keeping every stage's output cached and re-running only what changed.

## How it works

A clip is a **recipe**: `manifest.json` references source files for each layer.

```
project/
  manifest.json                   # the recipe
  00_SOURCE/                      # raw masters
  10_WORK/
    cuts.json                     # in/out per segment (editable)
    captions.ass                  # caption source (editable)
    music.json                    # music + leveling (editable)
    stages/                       # cached intermediate renders
      cut/<hash>.mp4
      reframe/<hash>.mp4
      grade/<hash>.mp4
      captions/<hash>.mp4
      mix/<hash>.mp4
      leadfix/<hash>.mp4
  20_DELIVER/v<N>/
    <Brand-named>.mp4
```

Engine reads manifest → for each stage in pipeline order:
1. Compute hash of (stage config + upstream stage output hash + stage version)
2. If `stages/<stage>/<hash>.mp4` exists → cache hit, skip render
3. Else → run stage, write to cached path
4. Pass output to next stage

Final stage's output is copied to `20_DELIVER/v<N>/<Brand-named>.mp4`.

## Usage

```bash
# First render of a new clip
python3 ${CLAUDE_PLUGIN_ROOT}/skills/render/engine.py <project_dir>

# Revise — engine auto-detects which source changed and rebuilds from there
python3 ${CLAUDE_PLUGIN_ROOT}/skills/render/engine.py <project_dir>

# Force re-render from a specific stage (skip cache from this stage forward)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/render/engine.py <project_dir> --from captions

# Deliver to a new version folder (default: v1; bumps if --bump)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/render/engine.py <project_dir> --bump
```

## Pipelines

Each clip type has a stage chain defined in `pipelines/<type>.json`. Project's `manifest.json` references one via `"pipeline": "listicle"`.

| Pipeline | Stages | Use for |
|---|---|---|
| `listicle` | cut → reframe → grade → captions → mix → leadfix → deliver | Rapid-fire numbered shorts (build_short.py output) |
| `qa` | cut → reframe → grade → captions → mix → leadfix → deliver | Single-cam Q&A / hotline from the stage cam (set `reframe.preset` to `stage`). Multicam Q&A is not a pipeline yet. |
| `podcast` (not built) | scene_split_reframe → dual_color_captions → mix → deliver | Two-speaker podcast. No `pipelines/podcast.json` yet. |
| `single` | cut → reframe → grade → captions → mix → leadfix → deliver | Single talking-head monologue (plain captions, no tabs) |
| `anthropic-demo` | remotion_render → audio_mix → deliver | Remotion-composited 16:9 product demo |

## Stage interface

Each stage is a Python module in `stages/<stage>.py` exposing:

```python
VERSION = "1.0.0"        # bump to invalidate cache for ALL prior outputs

def run(work_dir: Path, config: dict, inputs: dict) -> dict:
    """
    work_dir: the project's 10_WORK/ directory
    config:   this stage's config from the manifest
    inputs:   {"<prior_stage_name>": "/abs/path/to/output.mp4", ...}
    returns:  {"out": "/abs/path/to/this/stage/output.mp4"}
    """
```

Engine handles caching, hashing, and chaining. Stages are pure: given the same inputs+config+VERSION, they produce the same output.

## Manifest schema

See `manifest_example.json` for a working example. Required keys:

- `title` — short slug (e.g. `BusinessAdvice14Years`)
- `pipeline` — name matching a `pipelines/<name>.json`
- `output.name` — final delivered filename
- `output.dir` — relative to project, default `20_DELIVER/v1/`
- `stages` — per-stage config (keys match stage names in the pipeline)

## Revision workflow

1. Find the project folder for the clip
2. Identify which source file represents the layer that needs changing:
   - **Caption typo** → edit `10_WORK/captions.ass`
   - **Cut adjustment** → edit `10_WORK/cuts.json`
   - **Music change** → edit `10_WORK/music.json`
   - **Caption height / position** → `captions.preset` (static Y) or, on a split+close-up clip,
     `captions.closeup_y_pct` (close-up below-chin Y; split panels always ride the 50% seam). This is
     a BURN-only change → the GEN (transcribe+director) cache-HITs and is skipped (~60s saved, captions
     byte-identical). See the GEN/BURN split below.
   - **Reframe zoom/eye-y** → edit the `reframe` block in `manifest.json`
   - **Split-screen on a wide two-shot segment** → set `reframe.split` in `manifest.json`:
     `{"segments": [<cut-seg idx>], "top": {preset/roi/zoom/eye_y}, "bottom": {...}, "crop_y": 192, "detw": 2560}`
     (stage v2.4.x: full single-pass reframe still runs; flagged segments are rebuilt as two
     ROI-restricted qa_reframe_v2 tiles + h2v `make_splitscreen.py --width 2160`, spliced in
     frame-exactly; audio passes through untouched. Set each tile's `roi` from the MEASURED
     face positions in the wide shot — wrong ROI = silent ROI-center fallback, tile shows furniture.)
3. `python3 ${CLAUDE_PLUGIN_ROOT}/skills/render/engine.py <project>`
4. Engine rebuilds from the changed stage forward; ALL upstream stages cache-hit (instant)
5. New deliverable lands in `20_DELIVER/v<N>/`

### Caption GEN/BURN split (2026-06-12) — pixel-only revisions skip the director
The captions stage (`stages/captions.py` v2.4.0) splits caption work into two halves with different
dependencies, so a reframe/zoom/grade/caption-height tweak doesn't pay the ~40-80s LLM director again:
- **GEN** = transcribe → spice_format → LLM director (the styled caption file). Depends ONLY on the
  audio/words + context + corrections + caption-script versions — NOT on pixels. Cached in
  `10_WORK/caption_gen_cache/<genkey>/` keyed pixel-independently (cut-id, not the reframed video).
- **BURN** = layout + `generate_spice --burn` onto the framed video. Depends on the PIXELS; re-runs
  whenever the framing changes. Captions must sit on the final picture, so the burn can't be reused.
Result: a pixel-only re-render logs `caption-gen HIT — transcribe+director skipped` and goes straight
to the burn (faster + byte-identical captions, no director re-roll drift). A CUT change correctly
regenerates (audio/timing moved). `spice_caption.py` modes: `--gen-only`, `--burn-from <dir>`,
`--layout-file <json>` (full mode unchanged for the caption-app).

## Bumping a stage's VERSION

If the stage code changes (bug fix, new feature), bump its `VERSION` constant. The hash changes → all cached outputs for that stage become stale → next render re-runs that stage and everything downstream. This is the cache-invalidation switch.

## Backfill

Legacy delivered .mp4s (pre-render skill) have no manifest. Revisions on them use the overlay hack (caption layer over the burned video). Going forward, every NEW project goes through render.py and gets the layered cache.
