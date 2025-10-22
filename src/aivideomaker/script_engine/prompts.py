from __future__ import annotations

import json
from textwrap import dedent, indent
from typing import TYPE_CHECKING

from aivideomaker.article_ingest.model import ArticleBundle
from .model import ScriptPlan

if TYPE_CHECKING:
    from .reviewer import ScriptReviewDecision


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

    When you cite a fact or metric, lightly attribute its origin—reference the publication, dataset, research team,
    or institution in natural language (e.g., "According to Supply Chain Dive" or "Researchers at MIT found...").
    Give the viewer a clear path back to the source—mention the article's outlet or lead researchers once, and nod to
    any additional data providers when you call out their numbers. Keep it conversational; no formal footnotes needed.

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
{revision_context_block}
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


def _build_revision_context_block(
    review: "ScriptReviewDecision | None", previous_script: ScriptPlan | None
) -> str:
    if not review:
        return ""

    sections: list[str] = []
    review_lines = [
        "Revision context:",
        "The previous script attempt was rejected. Deliver a revised plan that resolves every concern and follows each action item.",
        f"Reviewer verdict: {review.verdict}",
    ]
    if review.summary:
        review_lines.append(f"Reviewer summary: {review.summary}")
    if review.strengths:
        review_lines.append("Retain these strengths when possible:")
        review_lines.extend(f"- {item}" for item in review.strengths)
    if review.concerns:
        review_lines.append("Blocking concerns to fix:")
        review_lines.extend(f"- {item}" for item in review.concerns)
    if review.action_items:
        review_lines.append("Required actions for the revision:")
        review_lines.extend(f"- {item}" for item in review.action_items)
    sections.append("\n".join(review_lines))

    if previous_script:
        script_payload = previous_script.model_dump(mode="json")
        script_json = json.dumps(script_payload, indent=2)
        sections.append("Previous script attempt (JSON):\n" + indent(script_json, "  "))

    block = "\n\n".join(sections)
    return "\n" + block + "\n\n"


def render_planning_prompt(
    bundle: ArticleBundle,
    excerpt_chars: int = 1800,
    review: "ScriptReviewDecision | None" = None,
    previous_script: ScriptPlan | None = None,
) -> str:
    article = bundle.article
    excerpt = article.text[:excerpt_chars]
    revision_context_block = _build_revision_context_block(review, previous_script)
    return SCRIPT_PLANNING_PROMPT.format(
        title=article.metadata.title,
        byline=article.metadata.byline or "Unknown",
        source=article.metadata.source or "Unknown",
        published=article.metadata.published_at or "Unknown",
        excerpt=excerpt,
        revision_context_block=revision_context_block,
    )


REVIEW_PROMPT_TEMPLATE = dedent(
    """
    You are the editorial gut-check ensuring the script plan still reflects the article's reporting.
    Given the original article and the proposed script plan, verify that the story the script tells
    matches the article's substance and key takeaways. Prioritize fidelity to the source over
    stylistic polish or suspense mechanics.

    Article metadata:
    - Title: {title}
    - Byline: {byline}
    - Source: {source}
    - Published: {published}

    Article synopsis:
    {synopsis}

    Script plan (JSON):
    {script_json}

    Respond ONLY with JSON using this schema:
    {{
      "verdict": "approve" or "revise",
      "summary": string,
      "strengths": [string, ...],
      "concerns": [string, ...],
      "action_items": [string, ...]
    }}

    Rules:
    - If the script introduces factual errors, contradicts the article, or omits the core takeaway, set "verdict" to "revise".
    - Approve when the script captures the article's main storyline, key facts, and nuance—even if pacing or suspense could improve.
    - Use "concerns" for each specific misalignment with the article. Mention beat ids when possible.
    - Use "action_items" to give concrete guidance to fix the factual or contextual gaps that block approval.
    - Keep the JSON concise; do not include explanatory prose outside the JSON object.
    """
)


def render_review_prompt(
    article: ArticleBundle, script: ScriptPlan, synopsis_chars: int = 800
) -> str:
    article_meta = article.article.metadata
    synopsis = article.article.text[:synopsis_chars].strip()
    script_payload = script.model_dump(mode="json")
    script_json = json.dumps(script_payload, indent=2)
    return REVIEW_PROMPT_TEMPLATE.format(
        title=article_meta.title,
        byline=article_meta.byline or "Unknown",
        source=article_meta.source or "Unknown",
        published=article_meta.published_at or "Unknown",
        synopsis=synopsis or "Synopsis unavailable.",
        script_json=indent(script_json, "  "),
    )
