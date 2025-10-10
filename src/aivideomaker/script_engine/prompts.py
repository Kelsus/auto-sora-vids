from __future__ import annotations

from textwrap import dedent

from aivideomaker.article_ingest.model import ArticleBundle


SCRIPT_PLANNING_PROMPT = dedent(
    """
    You are an investigative video script writer. Turn the provided article into a suspenseful narrative
    that hooks the viewer by foregrounding the controversy and withholding context as long as possible,
    without becoming misleading.

    Article metadata:
    - Title: {title}
    - Byline: {byline}
    - Source: {source}
    - Published: {published}

    Article excerpt (cleaned):
    {excerpt}

    Please respond with JSON using this schema:
    {{
      "premise": string,
      "controversy_summary": string,
      "withheld_context": string,
      "final_reveal": string,
      "beats": [
        {{
          "id": string,
          "purpose": string,
          "transcript": string,
          "suspense_level": integer (1-5),
          "estimated_duration_sec": number,
          "visual_seed": string,
          "audio_mood": string
        }}
      ]
    }}
    """
)


def render_planning_prompt(bundle: ArticleBundle, excerpt_chars: int = 1800) -> str:
    article = bundle.article
    excerpt = article.text[:excerpt_chars]
    return SCRIPT_PLANNING_PROMPT.format(
        title=article.metadata.title,
        byline=article.metadata.byline or "Unknown",
        source=article.metadata.source or "Unknown",
        published=article.metadata.published_at or "Unknown",
        excerpt=excerpt,
    )
