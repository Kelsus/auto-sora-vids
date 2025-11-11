# TODO (captions branch)

## Video length & style integration
- [x] **Plumb metadata from jobs → pipeline**
  - [x] Accept `pipeline_config.video_length` / `pipeline_config.video_style` (plus CLI flags) and surface them in `PipelineConfig` / `PipelineRunner` so `ScriptEngine` can read the operator’s choice.
  - [x] Normalize the four length presets (15/30/60/90s) into `target_runtime_sec`, `target_beat_count`, and per-beat duration guards; default to the existing 90s/6-beat profile when unset.
- [x] **Prompt templating**
  - [x] Refactor `SCRIPT_PLANNING_PROMPT` (src/aivideomaker/script_engine/prompts.py) to accept a `NarrativeStyleDirective` struct describing tone + structural rules.
  - [x] Implement directives for: docu-reveal (current behavior), how-to (stepwise instructional voice, capture procedural steps), and list (enumerated listicle tone, ensure every list item in the article is represented).
  - [x] Inject the runtime/beat guardrails dynamically so the LLM is explicitly told how many beats/seconds to aim for per style.
- [x] **Post-processing + chunker alignment**
  - [x] Extend `ScriptPlan` (model.py) or a companion metadata object to record the requested runtime/style so downstream planners can react—e.g., adjust beat pacing, chunk count, and chart assignments.
  - [x] After `ScriptPlan` validation, rescale `estimated_duration_sec` to the requested runtime if the LLM drifts, and ensure beat count matches the target profile (auto-trim or reflow when necessary).
- [x] **Docs + tests**
  - [x] Document the new options in README + docs/configuration.md, including how serverless jobs should pass them via `pipeline_config` and the CLI flags for local runs.
  - [x] Add unit tests for the prompt directive builder + duration normalizer, plus regression coverage that a 15s/how-to request produces fewer beats and an instructional tone.

Status: Karaoke ASS captions implemented and integrated.

Recent changes
- Added ASS/SSA karaoke builder using alignment payloads: `src/aivideomaker/captions/ass_builder.py`.
- Orchestrator writes `exports/captions.ass` when alignment is present and passes to stitcher.
- Stitcher burns ASS via ffmpeg (libass), preserving audio via stream copy.
- README updated with captions usage.

Verified
- Burned captions for run:
  - Input: `data/runs/us-sets-100-tariffs-for-china-linked-ship-to-shore-cranes-supply-chain-dive/exports/*.mp4`
  - Output: `.../exports/...captions.mp4` with yellow active-word highlighting.

Next steps
- Configurability
  - Expose font name/size, outline thickness, margins, alignment in config.
  - Add flag to enable/disable caption burn-in and to select filter (`ass` vs `subtitles`).
- Alternate timestamp sources
  - Optional importers for WhisperX / AssemblyAI word timings.
  - CLI: `--captions-from <json>` to override ElevenLabs alignment.
- Stitching behavior
  - Consider enabling auto-stitch for Sora non-dry runs once assets exist.
  - Single-pass encode path in stitcher when burning ASS (avoid pre-encode where possible).
- Fallback captions
  - When alignment missing, optionally build SRT from chunk timings or approximate word distribution.
- Reliability & DX
  - Handle Windows console emoji logging (cp1252) or strip emojis in non-UTF-8 consoles.
  - Add unit tests for `ass_builder` with synthetic alignments.

---

# Video Quality Upgrade Plan (from NextStepsConvo)

## 1. Establish creative guardrails
- [x] Draft style bible (`style_bible`) capturing visual palette, motion rules, camera/lens guidance, audio loudness targets, and caption styles.
- [x] Extend bundle schema with `style_bible`, `beats_meta_defaults`, `chart_specs`, and `qc_ruleset` blocks as outlined in NextStepsConvo.
- [x] Add per-beat metadata (`intent`, `visual.type`, negations, QC allowances, caption routing) to existing bundle generator.

## 2. Strengthen prompt + asset planning
- [x] Create reusable Sora prompt presets (8–10) aligned with style bible and embed default negations (no text/graphs/split-screen).
- [x] Implement beat-to-prompt mapping that selects presets based on `visual.type` and enforces min duration targets from `beats_meta_defaults`.
- [x] Introduce prompt linting to ensure every request carries negative constraints and camera grammar.

## 3. Add alternative visual tracks
- [x] Build still-motion module for `visual.type == "still_motion"` (Ken Burns/parallax from curated stills library).
- [x] Implement chart renderer for `visual.type == "chart"` using bundle `chart_specs` (e.g., Vega-Lite → MP4/PNG).
- [ ] Add asset caching layer so repeated beats can reuse approved Sora clips or charts.

### Serverless rollout
- [x] Split chart rendering into its own Lambda (reads bundle, writes chart asset, retries on AI API/transient failures).
- [x] Split still/Ken Burns generation into its own Lambda with retry support and shared asset storage.
- [x] Extend Step Functions map to run `chart -> still -> sora` per clip with individual retry policies.
- [x] Update job worker to skip inline chart/still generation when serverless helpers already produced assets.
- [x] Ensure new Lambdas can pull secrets/credentials (Gemini, OpenAI) from SSM and have write access to run prefixes.

## 4. Automated visual QC
- [ ] Add OCR-based check on Sora outputs; fail beats with unexpected text/numbers per beat QC rules.
- [ ] Detect split/dual screens or frame flicker (<0.12 s) using simple frame differencing.
- [ ] Enforce shot duration rules (min 1.25 s, max two sub-1.7 s shots consecutively); auto-regenerate when violated.

## 5. Audio pipeline polish
- [ ] Post-process VO for pacing: insert micro-pauses at beat boundaries and trim breaths.
- [ ] Align music cues with derived tempo curve; sidechain VO to meet `style_bible.audio` targets.
- [ ] Curate light SFX library and tag beats that should inject accents (whoosh, tactile).

## 6. Caption improvements
- [ ] Support dual caption styles (default vs. data) with safe-zone routing based on beat metadata.
- [ ] Enforce max 2 lines / 34 chars per line; split on phrase boundaries using timing data.
- [ ] Add logic to auto-move captions to top-safe region for chart beats.

## 7. Timeline assembly + exports
- [ ] Generate timeline EDL/XML from bundle, mapping picture, VO, music, SFX, and captions tracks.
- [ ] Apply J/L cuts and retimes based on beat cadence; ensure final mix hits loudness targets.
- [ ] Produce automated QC report summarizing flagged clips, durations, and source coverage before final render.

## 8. Cost and ops controls
- [ ] Route beats through cheapest viable generator (chart/still-motion first, Sora reserved for `cinematic_broll`).
- [ ] Batch Sora generations; run all non-Sora tracks immediately to parallelize workflow.
- [ ] Track clip similarity (e.g., CLIP embeddings) and reuse high-quality outputs when score above threshold.
