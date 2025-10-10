# Article-to-Video Pipeline Architecture

## High-Level Flow
1. **Article Ingestion** – Fetch and sanitize the target article, extract canonical metadata, and clean the body text.
2. **Story Crafting** – Use an LLM-driven prompt to craft a suspenseful script that foregrounds controversy, delays context, and maintains narrative tension.
3. **Chunk Planning** – Segment the script into beats that fit Sora 2 clip constraints while keeping VO cadence and transitions natural.
4. **Prompt Packaging** – For each beat, produce a Sora prompt containing:
   - Narration transcript (for TTS or cameo voice session)
   - Visual direction aligned with the beat
   - Audio guidance (music cues, SFX, energy level)
5. **Media Synthesis** – Submit prompts to Sora 2, capture the generated clips, and track metadata needed for downstream assembly.
6. **Post Production** – Stitch clips, enforce consistent voice track, mix audio levels, and export final deliverable into a drop folder.

## Key Components
- **`article_ingest`**: Handles URL normalization, HTTP fetch, readability parsing, and text cleanup.
- **`script_engine`**: Builds LLM prompts, orchestrates multi-step generation (hook → reveals → resolution), and outputs structured segments.
- **`chunker`**: Computes segment durations via estimated reading speed and Sora limits; enforces suspense pacing rules.
- **`prompt_builder`**: Maps each chunk into a structured payload for Sora and voice synthesis providers.
- **`orchestrator`**: CLI or service endpoint that glues the pipeline together and writes artifacts to disk.
- **`media_pipeline`**: Abstraction around Sora job submission, polling, and post-processing of returned assets.
- **`stitcher`**: Uses `ffmpeg` / `moviepy` to concatenate clips, align or synthesize voice track, and normalize audio.

## Storage Layout
```
./data/
  articles/       # Raw & cleaned article versions
  scripts/        # JSON outputs describing beats & prompts
  media/
    sora_clips/   # Generated video clips
    voice/        # Voice assets per cameo talent
  exports/        # Final rendered videos
```

## Extensibility Considerations
- Swappable LLM backends via a simple interface.
- Configurable beat lengths & suspense heuristics per platform.
- Hooks for future automation (feeds → scheduling → publishing).
- Rich logging/metrics trail for monitoring success rates.

## Next Steps
- Implement module skeletons with dependency injection for API clients.
- Define prompt templates and expected JSON schemas.
- Wire a simple CLI that executes the pipeline for a single article.
