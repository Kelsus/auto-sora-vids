from __future__ import annotations

from textwrap import dedent

from aivideomaker.article_ingest.model import ArticleBundle


CHART_ANALYSIS_TEMPLATE = dedent(
    """
    You are a video analytics editor. Read the following article and determine whether the finished
    video should include any literal charts or graphs on screen.

    IMPORTANT: Charts should be used SPARINGLY. Only propose a chart when:
    1. The article contains RICH quantitative data with 3+ distinct, comparable data points
    2. The data tells a story that is SIGNIFICANTLY enhanced by visualization (trends, comparisons, distributions)
    3. The numbers are central to the article's thesis, not incidental mentions

    DO NOT create charts for:
    - Single statistics (e.g., "only 4 watches" or "costs $2 million" - these are just facts, not chart data)
    - Round numbers used for emphasis rather than precision
    - Date ranges or time periods that don't show meaningful change over time
    - Counts or quantities that lack comparative context
    - Data that would result in a trivial visualization (e.g., a pie chart with only 2 slices)

    Workflow:
    1. Skim the article to understand the main thesis and supporting data.
    2. Identify data that genuinely benefits from visualization - look for comparisons, trends, or distributions
       with at least 3-4 distinct data points that reveal a pattern or insight.
    3. Be HIGHLY selective: most articles do NOT need charts. Only propose charts when the data is rich enough
       to create a meaningful, non-trivial visualization.
    4. For each chart, capture the concrete numbers, series, categories, or comparisons the article provides.
    5. Draft a tight code-interpreter prompt that tells OpenAI's Python sandbox exactly how to build the chart.

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
          "reason": string (explain why this data REQUIRES visualization - what pattern or insight does the chart reveal?),
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
    - Never propose more than {max_charts} charts. Most articles should have 0-1 charts.
    - Omit any chart if the article lacks trustworthy numeric inputs.
    - Every chart MUST include at least THREE data points with exact numeric values - no exceptions.
    - Single statistics or simple counts should NEVER become charts - just mention them in narration.
    - Choose variants that match the data structure (e.g., donut for composition, line for time series).
    - Keywords must be 3-6 concise terms that describe the data (topics, entities, metrics).
    - The code prompt should tell a Python data viz expert exactly how to plot the chart using the provided data points.
    - If no charts are needed (which is the common case), respond with {{"charts": []}}.
    - When in doubt, DO NOT include a chart. Prefer cinematic visuals over forced data visualization.
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
