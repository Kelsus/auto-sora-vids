from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from .orchestrator import PipelineOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate suspenseful video prompts from a news article URL."
    )
    parser.add_argument("url", help="URL of the news article to process")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/scripts"),
        help="Directory to write structured prompt bundle JSON",
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
    bundle = orchestrator.run(
        article_url=args.url,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        prompts_only=args.prompts_only,
    )

    output_path = args.output_dir / f"{bundle.article.slug}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = bundle.model_dump(mode="json")
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote prompt bundle to {output_path}")


if __name__ == "__main__":
    main()
