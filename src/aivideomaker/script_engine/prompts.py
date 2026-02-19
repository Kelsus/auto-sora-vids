from __future__ import annotations

import json
from textwrap import dedent, indent
from typing import TYPE_CHECKING

from aivideomaker.article_ingest.model import ArticleBundle
from .model import ScriptPlan
from .directives import (
    NarrativeStyleDirective,
    VideoLengthProfile,
    get_length_profile,
    get_style_directive,
)

if TYPE_CHECKING:
    from .reviewer import ScriptReviewDecision


SCRIPT_PLANNING_PROMPT = dedent(
    """
    You are a professional video script writer capable of sharp, high-trust narration across a variety of genres and styles.
    Turn the provided article into a video script tailored to the requested style and length profile.
    Hook the viewer with specificity: a concrete stake, a surprising fact, a clear question, or a vivid scene.
    Stay true to the article's content and do not become misleading.

{style_block}

    Writing rules (important):
    - Avoid corny marketing copy, influencer tease language, and canned transition phrases.
    - Do not use lines like: "And here's the crux", "But here's what they're not telling you", "Here's the thing",
      "Here's the problem", "Here's the catch", "You won't believe", "What happens next", "This changes everything",
      "Let that sink in", "Spoiler alert".
    - Prefer short, concrete sentences. Use specific nouns/verbs over hype. Avoid sensationalist or hyperbolic adjectives.
    - If you build intrigue, do it with verified facts and pacing—not insinuation.

    When you cite a fact or metric, lightly attribute its origin—reference the publication, dataset, research team,
    or institution in natural language (e.g., "According to Supply Chain Dive" or "Researchers at MIT found...").
    Give the viewer a clear path back to the source—mention the article's outlet or lead researchers once, and nod to
    any additional data providers when you call out their numbers. Keep it conversational; no formal footnotes needed.

    The narration must stay tight: aim for roughly {target_runtime_sec} seconds of voiceover (~{approx_words} spoken words total).
    Structure the story in exactly {target_beat_count} beats, each about {min_beat_sec}-{max_beat_sec} seconds, and include an "estimated_duration_sec"
    for every beat so the sum is <= {target_runtime_sec} seconds. If the article is long, condense aggressively—drop details
    rather than drifting past the timebox or adding extra beats. If the LLM drifts, you may be post-processed, so stay within the guardrails.

Runtime + pacing guardrails:
{runtime_block}

    Use escalating beats that move from surface signals into the diagnostic evidence. Sprinkle in concrete
    data points (inventories, freight indices, hiring stats, expected payback windows, etc.) that support the narrative arc.
    Every beat should either (a) surface a new fact that advances the thesis or (b) examine why stakeholders
    are reacting the way they are. Preserve the article's nuance: highlight both the seeming strength and the
    counterpoints the reporting surfaces. If the piece spotlights investments that will pay off over specific
    horizons, work those timeframes into the narration.

{chart_brief_block}
{character_block}
    Video-model content filters (critical):
    The `visual_seed`, `transcript`, and `audio_mood` fields are sent directly to a video
    generation model with strict word-level content filters. These filters flag individual
    words regardless of context, so even harmless phrases can be rejected if they contain
    a flagged word. Rules for these three fields:
    - Use only neutral, descriptive language. Avoid words associated with violence, weapons,
      conflict, crime, or inflammatory topics—even in metaphorical or business use.
    - Common replacements: "competition" not "battle/war/fight", "challenge" not "threat",
      "debate" not "controversy/conflict", "decline" not "collapse/crash", "surge" not
      "explosion", "setback" not "blow", "dismiss" not "fire" (personnel context),
      "disruption" not "destruction", "pressure" not "assault", "standoff" not "confrontation".
    - For `audio_mood`: use neutral musical terms (e.g., "contemplative underscore",
      "upbeat corporate score", "steady rhythmic pulse", "reflective piano") rather than
      dramatic terms (avoid "ominous", "menacing", "aggressive", "dark", "threatening").

    Article metadata:
    - Title: {title}
    - Byline: {byline}
    - Source: {source}
    - Published: {published}

    Article excerpt (cleaned):
    {excerpt}
{revision_context_block}
    Please respond with JSON using this schema:
    Label beats sequentially (e.g., "beat_1", "beat_2", ...), and keep ids stable across revisions when possible.
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


CHARACTER_VISUAL_BLOCK = dedent(
    """\
    Presenter character (important):
    The video features a consistent on-camera presenter who speaks each beat's transcript
    as dialogue. Every beat's `visual_seed` must describe the presenter actively
    illustrating or reacting to what they are saying—not just standing in a generic
    setting. The visual should make sense even on mute: a viewer should roughly understand
    the beat's topic from the presenter's actions, props, and environment alone.
    Good: "Presenter speaking to camera while holding up a tablet, shaking their head in disbelief, standing in a bright open-plan office with large windows and natural sunlight"
    Good: "Presenter walking energetically through a busy sunlit warehouse, pointing at shelves while workers move boxes behind them, animated gestures"
    Good: "Close-up of presenter outdoors on a rooftop with a city skyline behind them, leaning in to camera with an excited expression, golden hour light"
    Bad:  "Presenter in medium shot against a sleek tech lab backdrop" (generic, static, doesn't reflect transcript)
    Bad:  "Presenter standing in a dimly lit server room" (dark, static, boring)
    Bad:  "Massive shipping containers stacked at a port" (no presenter mentioned)
    Vary the presenter's framing (medium shot, close-up, walking, gesturing, interacting
    with objects) while always keeping them as the focal subject of the shot.

    Beat length (hard limit — count every word):
    Each beat is exactly 8 seconds of video. The AI voice speaks at ~1.5 words/second,
    so the absolute maximum is 12 words per beat transcript. Count the words before
    writing. Beats with 13+ words WILL be cut off mid-sentence. If a thought needs more
    room, split it across two beats. Distribute the story evenly; never front-load one beat.

    No real names (critical):
    The transcript and visual_seed are sent directly to the video generation model, which
    rejects prompts that mention real people by name. NEVER use real person names anywhere
    in the transcript or visual_seed. Instead, refer to people by their role, title, or
    affiliation. This applies to executives, politicians, researchers, celebrities, and
    any other named individuals. Product and company names are fine.
    Good: "The company's co-CEO called this 'just the beginning.'"
    Bad:  "Gustav Söderström called this 'just the beginning.'"
    Good: "According to the lead researcher, the results were surprising."
    Bad:  "According to John Smith, the results were surprising.\""""
)


def render_planning_prompt(
    bundle: ArticleBundle,
    excerpt_chars: int = 1800,
    review: "ScriptReviewDecision | None" = None,
    previous_script: ScriptPlan | None = None,
    chart_outline: str | None = None,
    *,
    style_directive: NarrativeStyleDirective | None = None,
    length_profile: VideoLengthProfile | None = None,
    has_character: bool = False,
    presenter_description: str | None = None,
) -> str:
    article = bundle.article
    excerpt = article.text[:excerpt_chars]
    revision_context_block = _build_revision_context_block(review, previous_script)
    directive = style_directive or get_style_directive(None)
    profile = length_profile or get_length_profile(None)
    style_block = indent(directive.prompt_block(), "    ")
    runtime_block = indent(profile.runtime_block(), "    ")
    # Character mode uses Veo-generated speech which is slower than TTS narration.
    approx_words = int(profile.target_beat_count * 12) if has_character else int(profile.target_runtime_sec * 2.1)
    chart_brief_block = ""
    if chart_outline:
        chart_brief_block = "Recommended charts (use each at most once, only when the beat's narration references the same data):\n" + indent(chart_outline, "    ") + "\n"
    if has_character:
        char_block = CHARACTER_VISUAL_BLOCK
        if presenter_description:
            char_block = f"Presenter identity: {presenter_description}\n" + char_block
        character_block = indent(char_block, "    ")
    else:
        character_block = ""
    return SCRIPT_PLANNING_PROMPT.format(
        title=article.metadata.title,
        byline=article.metadata.byline or "Unknown",
        source=article.metadata.source or "Unknown",
        published=article.metadata.published_at or "Unknown",
        excerpt=excerpt,
        revision_context_block=revision_context_block,
        chart_brief_block=chart_brief_block,
        character_block=character_block,
        style_block=style_block,
        runtime_block=runtime_block,
        target_runtime_sec=int(profile.target_runtime_sec),
        approx_words=approx_words,
        target_beat_count=profile.target_beat_count,
        min_beat_sec=f"{profile.min_beat_duration_sec:.0f}",
        max_beat_sec=f"{profile.max_beat_duration_sec:.0f}",
    )


REVIEW_PROMPT_TEMPLATE = dedent(
    """
    You are the editorial gut-check ensuring the script plan still reflects the article's reporting.
    Given the original article and the proposed script plan, verify that the story the script tells
    matches the article's substance and key takeaways. Prioritize fidelity to the source over
    stylistic polish or narrative pacing.

    Article metadata:
    - Title: {title}
    - Byline: {byline}
    - Source: {source}
    - Published: {published}

    Full article text:
    {article_text}

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
    - Approve when the script captures the article's main storyline, key facts, and nuance—even if pacing or narrative structure could improve.
    - Use "concerns" for each specific misalignment with the article. Mention beat ids when possible.
    - Use "action_items" to give concrete guidance to fix the factual or contextual gaps that block approval.
    - Keep the JSON concise; do not include explanatory prose outside the JSON object.
    """
)


def render_review_prompt(article: ArticleBundle, script: ScriptPlan) -> str:
    article_meta = article.article.metadata
    article_text = article.article.text.strip()
    script_payload = script.model_dump(mode="json")
    script_json = json.dumps(script_payload, indent=2)
    return REVIEW_PROMPT_TEMPLATE.format(
        title=article_meta.title,
        byline=article_meta.byline or "Unknown",
        source=article_meta.source or "Unknown",
        published=article_meta.published_at or "Unknown",
        article_text=article_text or "Article text unavailable.",
        script_json=indent(script_json, "  "),
    )
