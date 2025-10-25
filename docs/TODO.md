# TODO (captions branch)

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
- [ ] Create reusable Sora prompt presets (8–10) aligned with style bible and embed default negations (no text/graphs/split-screen).
- [x] Implement beat-to-prompt mapping that selects presets based on `visual.type` and enforces min duration targets from `beats_meta_defaults`.
- [ ] Introduce prompt linting to ensure every request carries negative constraints and camera grammar.

## 3. Add alternative visual tracks
- [ ] Build still-motion module for `visual.type == "still_motion"` (Ken Burns/parallax from curated stills library).
- [ ] Implement chart renderer for `visual.type == "chart"` using bundle `chart_specs` (e.g., Vega-Lite → MP4/PNG).
- [ ] Add asset caching layer so repeated beats can reuse approved Sora clips or charts.

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
