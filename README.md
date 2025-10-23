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
   - `exports/automated_review.json` — latest automated reviewer verdict and actionable feedback for revision.
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

## Serverless Orchestration (AWS CDK)
The `infra/` directory contains an AWS CDK app that wraps the downstream pipeline in a fully serverless workflow:
- API Gateway + Lambda endpoint to enqueue jobs in a DynamoDB table.
- EventBridge-triggered scheduler Lambda that promotes due jobs to the Step Functions state machine.
- Step Functions state machine coordinating prompt generation, per-clip video renders, and final stitching via the container-based Lambda worker.
- An S3-triggered Lambda that pushes the final rendered video into Google Drive using a service account.
- Shared Python layer (`infra/lambda_src/common_layer/`) provides DynamoDB helpers and time utilities consumed by every Lambda function.

### Deploy
1. Install CDK dependencies and bootstrap your target account/region if you have not already:
   ```bash
   cd infra
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cdk bootstrap aws://<account>/<region>
   ```
2. Deploy the stack (pass your AWS account and region via context):
   ```bash
   cdk deploy --context account=<account> --context region=<region>
   ```
3. After deployment:
   - Set the `GoogleDriveServiceAccountSecret` value to the raw JSON of a Drive-enabled service account (scope: `https://www.googleapis.com/auth/drive.file`).
   - Update the `GDRIVE_FOLDER_ID` environment variable on the `GoogleDriveForwarderLambda` function with the target Drive folder ID.
   - Create a SecureString parameter in AWS Systems Manager containing the Veo/Gemini service-account JSON, then redeploy with `--context veoCredentialsParameterName=/path/to/parameter`. The stack will pass the parameter name to the worker Lambda and grant it `ssm:GetParameter` permissions.
   - (Optional) Adjust the worker Lambda image (`infra/lambda_src/job_worker/Dockerfile`) to bundle codecs such as `ffmpeg` if you plan to run the pipeline end-to-end.
4. Run the lightweight unit tests for the Lambda modules:
   ```bash
   pytest infra/tests
   ```

### Using the API
Submit a job from any HTTPS client:
```bash
curl -X POST https://<api-id>.execute-api.<region>.amazonaws.com/prod/jobs \
  -H "Content-Type: application/json" \
  -d '{
        "url": "s3://my-bundles/how-generative-engine-optimization-geo-rewrites-the-rules/bundle.json",
        "scheduled_datetime": "2024-08-15T21:30:00Z",
        "pipeline_config": {
          "media_provider": "veo",
          "veo_aspect_ratio": "1:1"
        }
      }'
```
Jobs transition `PENDING → QUEUED → RUNNING → COMPLETED/FAILED` automatically. The worker stores all run artifacts in the provisioned S3 bucket under `jobs/<jobId>/run/`, and copies the final MP4 into `jobs/final/` (which triggers the Google Drive transfer Lambda).

The optional `pipeline_config` map mirrors the fields in `PipelineConfig`; any keys you include are applied only to that job.

## Troubleshooting
- `Missing Anthropics API key`: export `ANTHROPIC_API_KEY` or place it in a `.env` file before running the CLI.
- `Missing Sora API key`: export `OPENAI_API_KEY` (or update `sora_api_key_env`) before running without `--dry-run`
- New Claude model version? Adjust `llm_model` in your config JSON.
