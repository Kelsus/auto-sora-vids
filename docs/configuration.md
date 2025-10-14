# AI Video Maker Configuration

The pipeline reads optional JSON or YAML configuration files via the
`--config` flag on `aivideo`. Every key is optional—omitting a value falls
back to the defaults defined in `PipelineConfig`. This document lists all
available options and provides a sample configuration you can copy to
`config.local.json` (or any filename you pass on the CLI).

## Sample JSON

```json
{
  "data_root": "data/runs",
  "voice_id": "cameo_investigator",
  "llm_provider": "claude",
  "llm_model": "claude-sonnet-4-5",
  "anthropic_api_key_env": "ANTHROPIC_API_KEY",
  "media_provider": "sora",
  "negative_prompt": "no subtitles, no captions, no on-screen text, no watermark",
  "narration_voice_id": "FGY2WhTYpPnrIDTdsKH5",
  "elevenlabs_api_key_env": "ELEVEN_LABS_API_KEY",
  "narration_model_id": "eleven_turbo_v2",
  "narration_voice_settings": {
    "stability": 0.3,
    "similarity_boost": 0.75
  },
  "narration_enable_timestamps": true,
  "narration_audio_format": "mp3",
  "use_music": true,
  "music_api_key_env": "ELEVEN_LABS_API_KEY",
  "music_prompt": "Suspenseful investigative score with gradual build.",
  "music_track_duration_sec": 90.0,
  "music_model_id": "music_v1",
  "music_force_instrumental": true,
  "music_output_format": "mp3_44100_128",
  "music_request_timeout": 120.0,
  "use_real_sora": true,
  "sora_model": "sora-2",
  "sora_size": "720x1280",
  "sora_api_key_env": "OPENAI_API_KEY",
  "sora_poll_interval": 10.0,
  "sora_request_timeout": 60.0,
  "sora_max_wait": 600.0,
  "sora_submit_cooldown": 1.0,
  "veo_model": "veo-3.0-generate-001",
  "veo_api_key_env": "GOOGLE_API_KEY",
  "veo_aspect_ratio": "9:16",
  "veo_poll_interval": 10.0,
  "veo_max_wait": 600.0,
  "veo_max_concurrent_requests": 2,
  "veo_submit_cooldown": 0.0,
  "veo_use_vertex": true,
  "veo_project": "your-gcp-project",
  "veo_location": "us-central1",
  "veo_credentials_path": "google-api-key.json"
}
```

## Field Reference

- **`data_root`** – Root directory for per-article run folders (`data/runs` by default).
- **`voice_id` / `narration_voice_id`** – Default Cameo/ElevenLabs voice IDs for narration directives.
- **`llm_provider` / `llm_model` / `anthropic_api_key_env`** – LLM backend and environment variable used to load credentials.
- **`media_provider`** – `"sora"` or `"veo"` depending on which video backend to use for clip rendering.
- **`negative_prompt`** – Applied to every generated clip to avoid unwanted elements.
- **`elevenlabs_api_key_env`** – Environment variable name that stores the ElevenLabs narration key.
- **`narration_model_id`, `narration_voice_settings`, `narration_enable_timestamps`, `narration_audio_format`** – Detailed ElevenLabs narration controls.
- **`use_music`, `music_api_key_env`, `music_*`** – Toggle and configure ElevenLabs Music output.
- **`use_real_sora`, `sora_*`** – Turn on real Sora rendering and set OpenAI credentials, polling cadence, timeout, and size.
- **`veo_*`** – Settings for Google Veo (Gemini) rendering, including Vertex AI authentication.

You can mix and match these options—for example, keep `media_provider` as `"sora"`
but disable `use_music` while still generating narration. Store the configuration
file outside of version control (e.g., in `.gitignore`) to keep environment-specific
credentials and preferences local. When switching between Sora and Veo, update the
matching API key environment variable and rerun `aivideo --config <file>`.
