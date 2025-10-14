from __future__ import annotations

from textwrap import dedent

from aivideomaker.article_ingest.model import ArticleBundle


SCRIPT_PLANNING_PROMPT = dedent(
    """
    You are an investigative video script writer. Turn the provided article into a suspenseful narrative
    that hooks the viewer by foregrounding the controversy and withholding context as long as possible,
    without becoming misleading.

    Before drafting, articulate the article's central thesis in one sentence and list the top three fresh facts,
    quotes, or data points that prove it (including any specific numbers, time horizons, or stakeholder stances).
    Track these anchor insights through the outline so the finished script never loses the core argument.

    Before drafting, pinpoint the article's fresh insight versus widely known background facts.
    Make sure the withheld context and final reveal surface that unique angle and avoid presenting
    a well-known premise as the twist. When the story references earlier triggers (e.g., January frontloading),
    treat them as background context; the intrigue should center on why those past moves still matter today
    and what new metrics prove it. Anchor the beats in clear timeline cues so viewers grasp why the story
    matters right now and how earlier events are still rippling through the data. When you fill
    `withheld_context` and `final_reveal`, spotlight the new evidence the article surfaces—ongoing inventory
    gluts, freight slowdowns, hiring freezes—using the earlier trigger only as setup, not the punchline.

    Never invent conflict: ground the controversy in the article's documented tension (e.g., executives delaying
    supply-chain moves until tariffs settle, even as they rush funding toward AI pilots). Ensure the
    `controversy_summary` contrasts the clashing priorities accurately. The `final_reveal` must restate the
    article's primary takeaway in plain language, supported by at least one of the fresh facts you identified.

    The narration must stay tight: aim for roughly 90 seconds of voiceover (~190 spoken words total).
    Structure the story in exactly 6 beats, each about 12-15 seconds, and include an "estimated_duration_sec"
    for every beat so the sum is <= 90 seconds. If the article is long, condense aggressively—drop details
    rather than drifting past the timebox or adding extra beats.

    Use escalating beats that move from surface signals into the diagnostic evidence. Sprinkle in concrete
    data points (inventories, freight indices, hiring stats, expected payback windows, etc.) that support the tension.
    Every beat should either (a) surface a new fact that advances the thesis or (b) interrogate why stakeholders
    are reacting the way they are. Preserve the article's nuance: highlight both the seeming strength and the
    warning signs the reporting surfaces. If the piece spotlights investments that will pay off over specific
    horizons, work those timeframes into the narration.

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

    For `social_caption`, write a multi-line caption that opens with a punchy headline line, follows with 4-6 bullet points
    (each starting with "•") that cite concrete stats, actions, or contradictions from the story, and closes with a one-line
    takeaway after a blank line. Provide 5-8 relevant hashtags without the leading '#'.
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
