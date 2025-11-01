# Chart Composition Workflow Implementation

## Overview

This document describes the new chart composition workflow that creates professional, contextual chart presentations for videos.

## Architecture

### Old Workflow
1. Generate chart PNG
2. Pass chart PNG to Sora as reference image
3. Use standard Sora prompt with chart

### New Workflow
1. **Generate chart PNG** (existing)
2. **Transform Sora prompt → Static scene description** (Claude)
3. **Generate composite image** (Gemini Flash 2.5 Image) - Chart in professional scene
4. **Create animation prompt** (Claude) - Sora instructions for chart reveal
5. **Pass composite + animation to Sora** → Professional animated chart scene

## Components Created

### 1. GeminiImageClient (`src/aivideomaker/media_pipeline/gemini_image_client.py`)
- Generates composite images using Gemini Flash 2.5
- Takes: static scene description + chart PNG
- Returns: composite image with chart in context
- Uses Vertex AI authentication (same as Veo)

### 2. Prompt Engineering Functions (`src/aivideomaker/prompt_builder/prompt_transform.py`)
- `convert_sora_prompt_to_static_scene()` - Uses Claude to remove motion/temporal elements
- `create_chart_animation_prompt()` - Uses Claude to create chart-specific animations
- Fallback to rule-based transformation if Claude unavailable

### 3. MediaPrompt Model Updates (`src/aivideomaker/prompt_builder/model.py`)
Added fields:
- `static_scene_prompt` - For Gemini image generation
- `animation_prompt` - For Sora animation
- `composite_image_path` - Path to generated composite

### 4. Unified Composite Worker (`backend/lambda_src/composite_worker/`)
- Replaces both `chart_worker` and `still_worker`
- Handles preprocessing for both chart and still_motion clips
- Routes to appropriate workflow based on visual type

### 5. Orchestrator Updates (`src/aivideomaker/orchestrator.py`)
- Added `_compose_chart_scene()` method - Full composition workflow
- Updated `render_clip()` to use new workflow for charts
- Added Gemini Image client initialization
- Added LLM client to orchestrator for prompt engineering

### 6. Step Function Updates (`backend/pipeline_stack.py`)
- Replaced `chart_clip_task` + `still_clip_task` with single `composite_clip_task`
- Updated Lambda: `CompositeAssetLambda` (15min timeout, 4GB memory)
- Workflow: `composite → sora → stitch`

## Configuration

### New PipelineConfig Fields
```python
# Gemini Image configuration
gemini_image_model: str = "gemini-2.0-flash-exp"
gemini_use_vertex: bool = True
gemini_project: Optional[str] = None  # Defaults to veo_project
gemini_location: str = "us-central1"
gemini_credentials_path: Optional[Path] = None  # Defaults to veo_credentials_path
gemini_api_key_env: str = "GOOGLE_API_KEY"
```

## Workflow Details

### Chart Composition Process

```
1. Generate Chart PNG
   ├─ OpenAI Chart Client (if enabled)
   └─ Local Chart Renderer (fallback)

2. Transform Prompt (Claude)
   Input: "Slow pan across office revealing data dashboard..."
   Output: "Professional office with data dashboard on wall..."
   
3. Generate Composite (Gemini)
   Input: Static scene prompt + Chart PNG
   Output: Composite image (chart in scene)
   
4. Create Animation (Claude)
   Output: "Starting from this image, camera pushes toward dashboard.
            Chart animates: bars rise from bottom, labels fade in..."
            
5. Sora Rendering
   Input: Composite image + Animation prompt
   Output: Professional video with animated chart reveal
```

### Claude Prompt Engineering

**Static Scene Transform:**
- Removes: camera movements, temporal actions, motion verbs
- Preserves: spatial composition, lighting, atmosphere
- Adds: chart placement instructions

**Animation Creation:**
- Varies by chart type (bar, line, pie, etc.)
- Includes camera movement (subtle push/reveal)
- Specifies data animation sequence
- Maintains professional aesthetic

## Error Handling

- **No fallbacks for Gemini failures** - Step function fails loudly
- Chart PNG generation failures → logged warning, continues
- Claude unavailable → falls back to rule-based transformations
- Validation errors → raised immediately

## Benefits

1. **Contextual Integration** - Charts appear in professional scenes (offices, studios, etc.)
2. **Dynamic Animations** - Each chart gets custom animation via Claude creativity
3. **Better Composition** - Gemini creates cohesive scene+chart composites
4. **Unified Processing** - Single worker handles all composite needs
5. **Scalable** - Uses Claude/Gemini for intelligence, maintains quality

## Testing Considerations

1. Test chart types: bar, line, pie, donut, area
2. Verify Gemini composite quality
3. Validate Claude prompt transformations
4. Check animation variety across charts
5. Ensure step function resilience
6. Verify memory/timeout sufficiency (15min, 4GB)

## Future Enhancements

1. Add retry logic for Gemini failures
2. Cache successful compositions
3. Quality scoring for composites
4. Animation templates library
5. A/B testing different scene styles

## Files Modified

### Created
- `src/aivideomaker/media_pipeline/gemini_image_client.py`
- `src/aivideomaker/prompt_builder/prompt_transform.py`
- `backend/lambda_src/composite_worker/`

### Modified
- `src/aivideomaker/media_pipeline/__init__.py`
- `src/aivideomaker/prompt_builder/model.py`
- `src/aivideomaker/orchestrator.py`
- `backend/pipeline_stack.py`

### Replaced
- ~~`backend/lambda_src/chart_worker/`~~ → `composite_worker`
- ~~`backend/lambda_src/still_worker/`~~ → `composite_worker`

