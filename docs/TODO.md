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

