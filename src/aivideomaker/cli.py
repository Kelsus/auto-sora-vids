from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from .orchestrator import PipelineBundle, PipelineOrchestrator, ScriptRejectedError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate suspenseful video prompts from a news article URL."
    )
    parser.add_argument("url", nargs="?", help="URL of the news article to process")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/runs"),
        help="Base directory for per-article outputs",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional path to pipeline configuration JSON/YAML",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip media generation and only emit planning artifacts",
    )
    parser.add_argument(
        "--prompts-only",
        action="store_true",
        help="Stop after generating prompts and write them to disk without preparing voice assets or contacting Sora",
    )
    parser.add_argument(
        "--prompt-bundle",
        type=Path,
        help="Path to a previously generated bundle JSON to execute against Sora",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete existing artifacts for this URL before regenerating",
    )
    parser.add_argument(
        "--stitch-only",
        action="store_true",
        help="Skip media submission and only stitch existing assets",
    )
    return parser


def main() -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    orchestrator = (
        PipelineOrchestrator.from_file(args.config)
        if args.config
        else PipelineOrchestrator.default()
    )

    if args.prompt_bundle:
        payload = json.loads(args.prompt_bundle.read_text(encoding="utf-8"))
        bundle = PipelineBundle.model_validate(payload)
        bundle = orchestrator.execute_prompts(
            bundle=bundle,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
            prompts_only=args.prompts_only,
            cleanup=args.cleanup,
            stitch_only=args.stitch_only,
        )
    else:
        if not args.url:
            parser.error("Either a URL or --prompt-bundle must be provided")
        try:
            bundle = orchestrator.run(
                article_url=args.url,
                output_dir=args.output_dir,
                dry_run=args.dry_run,
                prompts_only=args.prompts_only,
                cleanup=args.cleanup,
                stitch_only=args.stitch_only,
            )
        except ScriptRejectedError as exc:
            print(str(exc))
            return

    run_dir = (args.output_dir / bundle.article.slug)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "bundle.json"
    payload = bundle.model_dump(mode="json")
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote prompt bundle to {output_path}")


if __name__ == "__main__":
    main()
