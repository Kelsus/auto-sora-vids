from __future__ import annotations

from textwrap import dedent

from aivideomaker.article_ingest.model import ArticleBundle


CHART_ANALYSIS_TEMPLATE = dedent(
    """
    You are a video analytics editor. Read the following article and determine whether the finished
    video should include any literal charts or graphs on screen. Focus on quantitative claims that a viewer
    would expect to see visualized.

    Workflow:
    1. Skim the article to understand the main thesis and supporting data.
    2. Identify every chart the article might suggest, then pick the {max_charts} that are most critical to
       communicating the article's core ideas. Only include charts that are strongly implied or directly supported
       by the reporting. Skip speculative or redundant visuals.
    3. For each chart, capture the concrete numbers, series, categories, or comparisons the article provides.
    4. Draft a tight code-interpreter prompt that tells OpenAI's Python sandbox exactly how to build the chart
       from the provided data—no missing numbers.
    5. Provide search keywords that will help match this chart to the best narrative beat later.

    Article metadata:
    - Title: {title}
    - Source: {source}
    - Published: {published}

    Article excerpt:
    {excerpt}

    Respond with JSON using this schema:
    {{
      "charts": [
        {{
          "id": string (unique slug, e.g., "ai-layoffs-share"),
          "title": string,
          "summary": string,
          "reason": string,
          "variant": string ("bar", "line", "donut", etc.),
          "subtitle": string | null,
          "note": string | null,
          "x_label": string | null,
          "y_label": string | null,
          "source": string | null,
          "keywords": [string, ...],
          "data_points": [{{
            "label": string,
            "value": number,
            "secondary_value": number | null,
            "series": string | null
          }}],
          "code_prompt": string
        }}
      ]
    }}

    Rules:
    - Never propose more than {max_charts} charts. If the article mentions more data, choose the set that best supports the main thesis.
    - Omit any chart if the article lacks trustworthy numeric inputs.
    - Every chart must include at least two data points with exact numeric values taken from the article or its cited sources.
    - Choose variants that match the data structure (e.g., donut for composition, line for time series).
    - Keywords must be 3-6 concise terms that describe the data (topics, entities, metrics).
    - The code prompt should tell a Python data viz expert exactly how to plot the chart using the provided data points.
    - If no charts are needed, respond with {{"charts": []}}.
    """
)


def render_chart_analysis_prompt(
    bundle: ArticleBundle,
    *,
    max_charts: int = 3,
    excerpt_chars: int = 2600,
) -> str:
    article = bundle.article
    excerpt = article.text[:excerpt_chars]
    return CHART_ANALYSIS_TEMPLATE.format(
        max_charts=max_charts,
        title=article.metadata.title,
        source=article.metadata.source or "Unknown",
        published=article.metadata.published_at or "Unknown",
        excerpt=excerpt,
    )


__all__ = ["render_chart_analysis_prompt"]
