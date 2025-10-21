# AI Video Maker

Pipeline for turning news articles into suspenseful, controversy-led video scripts and Sora 2 prompt bundles.

## Features (MVP)
- Fetches and cleans article content from a URL.
- Converts PDF reports to plain text before downstream processing.
- Repairs minor JSON formatting issues returned by the LLM before validation.
- Crafts a multi-beat script that delays context for maximum tension (LLM-backed, pluggable).
- Segments beats into Sora-sized clips with estimated durations and suspense metadata.
- Outputs structured prompts including transcript, visual direction, audio guidance, and voice directives.
- Dry-run media pipeline that stubs Sora 2 clip generation and prepares a unified voice transcript for cameo capture.
- Hooks for stitching clips and leveling audio once real media assets exist.
- Karaoke captions (ASS/SSA) with word-level highlight when alignment is available; burned-in via ffmpeg/libass.

## Project Layout
```
src/aivideomaker/
  article_ingest/   # Article fetching & cleanup
  script_engine/    # LLM prompt templates & parsing
  chunker/          # Beat-to-clip planning
  prompt_builder/   # Sora prompt packaging & voice directives
  media_pipeline/   # Sora + voice placeholders
  stitcher/         # ffmpeg/moviepy-based assembly
  orchestrator.py   # End-to-end coordination
  cli.py            # `aivideo` entry point
```

## Getting Started
1. **Install dependencies** (Python 3.10+):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```
2. **Configure Claude Sonnet 4.5 access:**
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```
   You can also create a `.env` file with this key; the CLI loads it automatically.

   To hit the real Sora API, add an OpenAI key with Sora 2 access:
   ```bash
   export OPENAI_API_KEY="sk-openai-..."
   ```

3. **Run the pipeline (dry-run by default):**
   ```bash
   aivideo "https://example.com/news/article"
   ```
   The command writes a prompt bundle and run artifacts into `data/runs/<slug>/`, including:
   - `bundle.json` — serialized article, script, prompts, and media metadata.
   - `media/sora_clips/` — generated or placeholder video clips.
   - `media/voice/` — narration transcript, audio, and alignment data.
   - `media/music/` — optional music tracks (when enabled).
   - `exports/` — stitched outputs and social caption drafts.

   To review prompts without touching Sora or generating transcripts, add `--prompts-only`:
   ```bash
   aivideo "https://example.com/news/article" --prompts-only
   ```
   That variant stops after prompt generation and simply writes the JSON bundle for inspection.

Once satisfied with the prompts, you can render them later from the saved JSON bundle:
   ```bash
   aivideo --prompt-bundle data/runs/example-article/bundle.json --dry-run   # placeholder artifacts
   aivideo --prompt-bundle data/runs/example-article/bundle.json              # contacts Sora if enabled
   ```

### Captions
- When ElevenLabs timestamps are enabled and narration is rendered (non-dry-run), the pipeline creates `exports/captions.ass` and burns it into the final video with libass. Active word is highlighted (yellow), with white text and black outline.
- To customize fonts, drop font files into `data/runs/<slug>/exports/fonts/`. The burner passes `fontsdir` to ffmpeg so libass can discover them.
- Reference burn-in command (standalone):
  `ffmpeg -i input.mp4 -vf "subtitles=karaoke.ass:fontsdir=./fonts" -c:v libx264 -crf 18 -preset slow -c:a copy output.mp4`

4. **Enable real integrations** (future work):
   - Add alternate LLM backends (e.g., OpenAI) or multi-pass planning prompts if needed.
   - Tune Sora 2 render parameters (seconds, size, batching) for production workflows.
   - Integrate cameo voice or ElevenLabs within `media_pipeline/voice.py`.
   - Replace the dry-run guard in `orchestrator.PipelineOrchestrator.run` with actual stitching logic once clips exist.

## Configuration
You can supply a JSON or YAML config to override the data root, voice, or Claude settings:
```json
{
  "data_root": "./data",
  "voice_id": "cameo_investigator",
  "sora_model": "sora-2",
  "sora_size": "1280x720",
  "llm_provider": "claude",
  "llm_model": "claude-sonnet-4-5",
  "anthropic_api_key_env": "ANTHROPIC_API_KEY",
  "sora_api_key_env": "OPENAI_API_KEY"
}
```
Run with:
```bash
aivideo <url> --config config.json
```

For a two-step workflow, run once with `--prompts-only`, review the JSON bundle, then replay it with `--prompt-bundle` when you are ready to call Sora.

## Roadmap Ideas
- Automated polling of RSS/Atom feeds for trending stories.
- Multi-voice cameo support with diarization-aware stitching.
- Automatic distribution hooks for YouTube Shorts, TikTok, and Instagram Reels.
- Quality gates that validate narrative tension, fact coverage, and pacing before publishing.

## Development Status (captions branch)
- Karaoke captions (ASS/SSA) with per-word highlighting are implemented and burned via ffmpeg/libass when ElevenLabs alignment is available.
- See `docs/TODO.md` for current tasks and next steps on caption styling/configuration and alternate timestamp sources (WhisperX/AssemblyAI).

## Troubleshooting
- `Missing Anthropics API key`: export `ANTHROPIC_API_KEY` or place it in a `.env` file before running the CLI.
- `Missing Sora API key`: export `OPENAI_API_KEY` (or update `sora_api_key_env`) before running without `--dry-run`
- New Claude model version? Adjust `llm_model` in your config JSON.
