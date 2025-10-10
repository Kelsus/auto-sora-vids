# AI Video Maker

Pipeline for turning news articles into suspenseful, controversy-led video scripts and Sora 2 prompt bundles.

## Features (MVP)
- Fetches and cleans article content from a URL.
- Crafts a multi-beat script that delays context for maximum tension (LLM-backed, pluggable).
- Segments beats into Sora-sized clips with estimated durations and suspense metadata.
- Outputs structured prompts including transcript, visual direction, audio guidance, and voice directives.
- Dry-run media pipeline that stubs Sora 2 clip generation and prepares a unified voice transcript for cameo capture.
- Hooks for stitching clips and leveling audio once real media assets exist.

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

3. **Run the pipeline (dry-run by default):**
   ```bash
   aivideo "https://example.com/news/article"
   ```
   The command writes a JSON bundle into `data/scripts/<slug>.json`, creates placeholder Sora assets under `data/media/sora_clips/`, and stores a combined narration transcript in `data/media/voice/`.

   To review prompts without touching Sora or generating transcripts, add `--prompts-only`:
   ```bash
   aivideo "https://example.com/news/article" --prompts-only
   ```
   That variant stops after prompt generation and simply writes the JSON bundle for inspection.

4. **Enable real integrations** (future work):
   - Add alternate LLM backends (e.g., OpenAI) or multi-pass planning prompts if needed.
   - Implement Sora 2 API submission inside `media_pipeline/sora_client.py`.
   - Integrate cameo voice or ElevenLabs within `media_pipeline/voice.py`.
   - Replace the dry-run guard in `orchestrator.PipelineOrchestrator.run` with actual stitching logic once clips exist.

## Configuration
You can supply a JSON or YAML config to override the data root, voice, or Claude settings:
```json
{
  "data_root": "./data",
  "voice_id": "cameo_investigator",
  "use_real_sora": false,
  "llm_provider": "claude",
  "llm_model": "claude-sonnet-4-5",
  "anthropic_api_key_env": "ANTHROPIC_API_KEY"
}
```
Run with:
```bash
aivideo <url> --config config.json
```

## Roadmap Ideas
- Automated polling of RSS/Atom feeds for trending stories.
- Multi-voice cameo support with diarization-aware stitching.
- Automatic distribution hooks for YouTube Shorts, TikTok, and Instagram Reels.
- Quality gates that validate narrative tension, fact coverage, and pacing before publishing.

## Troubleshooting
- `Missing Anthropics API key`: export `ANTHROPIC_API_KEY` or place it in a `.env` file before running the CLI.
- New Claude model version? Adjust `llm_model` in your config JSON.
