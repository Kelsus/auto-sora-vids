from __future__ import annotations

from textwrap import dedent

from aivideomaker.article_ingest.model import ArticleBundle


SCRIPT_PLANNING_PROMPT = dedent(
    """
    You are an investigative video script writer. Turn the provided article into a suspenseful narrative
    that hooks the viewer by foregrounding the controversy and withholding context as long as possible,
    without becoming misleading.

    Before drafting, pinpoint the article's fresh insight versus widely known background facts.
    Make sure the withheld context and final reveal surface that unique angle and avoid presenting
    a well-known premise as the twist. When the story references earlier triggers (e.g., January frontloading),
    treat them as background context; the intrigue should center on why those past moves still matter today
    and what new metrics prove it. Anchor the beats in clear timeline cues so viewers grasp why the story
    matters right now and how earlier events are still rippling through the data. When you fill
    `withheld_context` and `final_reveal`, spotlight the new evidence the article surfaces—ongoing inventory
    gluts, freight slowdowns, hiring freezes—using the earlier trigger only as setup, not the punchline.

    The narration must stay tight: aim for roughly 90 seconds of voiceover (~190 spoken words total).
    Structure the story in exactly 6 beats, each about 12-15 seconds, and include an "estimated_duration_sec"
    for every beat so the sum is <= 90 seconds. If the article is long, condense aggressively—drop details
    rather than drifting past the timebox or adding extra beats.

    Use escalating beats that move from surface signals into the diagnostic evidence. Sprinkle in concrete
    data points (inventories, freight indices, hiring stats, etc.) that support the tension. Preserve the
    article's nuance: highlight both the seeming strength and the slowdown indicators the reporting surfaces.

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
      ],
      "social_caption": {{
        "description": string,
        "hashtags": [string, ...]
      }}
    }}

    Keep the social caption short (≤100 characters) and supply 5-8 relevant hashtags without the leading '#'.
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
