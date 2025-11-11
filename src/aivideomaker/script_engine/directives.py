from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Sequence

from .model import Beat, ScriptPlan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoLengthProfile:
    key: str
    target_runtime_sec: float
    target_beat_count: int
    min_beat_duration_sec: float
    max_beat_duration_sec: float
    default_beat_duration_sec: float

    def runtime_block(self) -> str:
        return (
            f"- Target runtime: ~{int(self.target_runtime_sec)} seconds.\n"
            f"- Structure exactly {self.target_beat_count} beats; do not add extras.\n"
            f"- Keep `estimated_duration_sec` between {self.min_beat_duration_sec:.1f}s and {self.max_beat_duration_sec:.1f}s "
            f"so the total stays under {int(self.target_runtime_sec)} seconds."
        )


@dataclass(frozen=True)
class NarrativeStyleDirective:
    style_id: str
    display_name: str
    framing: str
    tone_rules: Sequence[str]
    structure_rules: Sequence[str]
    finishing_notes: Sequence[str] | None = None

    def prompt_block(self) -> str:
        tone_lines = "\n".join(f"  - {line}" for line in self.tone_rules)
        structure_lines = "\n".join(f"  - {line}" for line in self.structure_rules)
        notes_lines = ""
        if self.finishing_notes:
            notes_lines = "\nSpecial notes:\n" + "\n".join(f"  - {line}" for line in self.finishing_notes)
        return (
            f"This run uses the **{self.display_name}** mode. {self.framing}\n"
            f"Tone & pacing cues:\n{tone_lines}\n"
            f"Structural expectations:\n{structure_lines}{notes_lines}"
        )


VIDEO_LENGTH_PRESETS: dict[str, VideoLengthProfile] = {
    "15s": VideoLengthProfile(
        key="15s",
        target_runtime_sec=15.0,
        target_beat_count=3,
        min_beat_duration_sec=3.0,
        max_beat_duration_sec=6.0,
        default_beat_duration_sec=5.0,
    ),
    "30s": VideoLengthProfile(
        key="30s",
        target_runtime_sec=30.0,
        target_beat_count=4,
        min_beat_duration_sec=5.0,
        max_beat_duration_sec=8.0,
        default_beat_duration_sec=7.0,
    ),
    "60s": VideoLengthProfile(
        key="60s",
        target_runtime_sec=60.0,
        target_beat_count=5,
        min_beat_duration_sec=8.0,
        max_beat_duration_sec=14.0,
        default_beat_duration_sec=12.0,
    ),
    "90s": VideoLengthProfile(
        key="90s",
        target_runtime_sec=90.0,
        target_beat_count=6,
        min_beat_duration_sec=12.0,
        max_beat_duration_sec=18.0,
        default_beat_duration_sec=15.0,
    ),
}


NARRATIVE_STYLE_DIRECTIVES: dict[str, NarrativeStyleDirective] = {
    "docu_reveal": NarrativeStyleDirective(
        style_id="docu_reveal",
        display_name="Docu-Reveal",
        framing=(
            "Play investigative storyteller: foreground the tension, drip withheld context, and land on the article's "
            "primary revelation without inventing drama."
        ),
        tone_rules=(
            "Open with urgency; hint that something important is being hidden in plain sight.",
            "Use confident, reporterly narration with light skepticism and specific sourcing cues.",
            "Let suspense rise beat by beat until the final reveal resolves the tension.",
        ),
        structure_rules=(
            "Beat 1 is the hook with a provocative question or unexpected stake.",
            "Middle beats peel back evidence (data, quotes, timelines) that explain the controversy.",
            "Penultimate beat surfaces the withheld context; final beat states the takeaway and what it means next.",
        ),
        finishing_notes=(
            "Whenever you cite data, attribute it conversationally (e.g., 'According to...').",
            "Keep narration conversational and avoid courtroom theatrics—this is reported insight, not sensationalism.",
        ),
    ),
    "how_to": NarrativeStyleDirective(
        style_id="how_to",
        display_name="How-To Explainer",
        framing=(
            "Teach the viewer how to replicate the article's playbook. Every beat must be an actionable step or principle,"
            " ordered from setup to payoff."
        ),
        tone_rules=(
            "Second-person coaching voice ('you', 'your team').",
            "Frame every claim as immediately useful and grounded in the article's proof.",
            "Blend authority with encouragement—make the viewer feel guided by a sharp operator.",
        ),
        structure_rules=(
            "Label beats sequentially (Step 1, Step 2, etc.) in the prose itself.",
            "Each beat introduces one discrete move, backed by the article's evidence or quotes.",
            "Final beat summarizes the checklist and stakes the payoff if the viewer follows it.",
        ),
        finishing_notes=(
            "Avoid suspense wording; clarity and momentum matter more than mystery.",
            "If the article lacks literal steps, distill its main arguments into operator tasks.",
        ),
    ),
    "listicle": NarrativeStyleDirective(
        style_id="listicle",
        display_name="List Spotlight",
        framing=(
            "Deliver the story as a countdown or ranked list. Highlight distinct items or actors and why each matters now."
        ),
        tone_rules=(
            "Punchy, confident narration with quick pivots between items.",
            "Use vivid descriptors so each item feels visually distinct.",
            "Call out stakes or surprises per item—what makes this entry list-worthy?",
        ),
        structure_rules=(
            "Open with a framing beat that sets up the list and why it's urgent.",
            "Each subsequent beat focuses on one list item with a headline-style setup followed by receipts.",
            "Close with a synthesis beat that ties the list back to the thesis or previews what happens next.",
        ),
        finishing_notes=(
            "Reference the original ordering if the article provides one; otherwise choose an order that escalates drama.",
            "Make sure every promised list item actually appears in the beats.",
        ),
    ),
    "first_person": NarrativeStyleDirective(
        style_id="first_person",
        display_name="First Person Story",
        framing=(
            "Channel the article's narrator directly. Reference their name and publication up top, speak in first-person when relaying their experience, and weave in their internal reactions alongside the data they cite."
        ),
        tone_rules=(
            "Use conversational first-person language (\"I\", \"me\", \"my\") when echoing the author's experience; switch to third person only when adding external context.",
            "Highlight sensory details, emotions, and micro-observations exactly as the writer described them.",
            "Balance intimacy with reportage -- tie personal revelations back to broader stakes or trends the article documents.",
        ),
        structure_rules=(
            "Beat 1 introduces the author, outlet, setting, and the spark for their realization.",
            "Middle beats follow the journey chronologically -- what they noticed, how it felt, the contradictions they wrestled with.",
            "Resolution beat connects the personal epiphany to a bigger takeaway for the viewer (what this means for riders, workers, or the industry).",
        ),
        finishing_notes=(
            "Quote short lines from the piece when they capture the narrator's voice; make sure attribution stays clear.",
            "Invite the viewer into the moment (\"here's what I saw\" / \"here's when it clicked\") while still fact-checking against the article.",
        ),
    ),
}

DEFAULT_LENGTH_KEY = "90s"
DEFAULT_STYLE_KEY = "docu_reveal"
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def get_length_profile(name: str | None) -> VideoLengthProfile:
    if not name:
        return VIDEO_LENGTH_PRESETS[DEFAULT_LENGTH_KEY]
    key = _normalize_key(name)
    profile = VIDEO_LENGTH_PRESETS.get(key)
    if profile:
        return profile
    logger.warning("Unknown video_length '%s'; falling back to %s", name, DEFAULT_LENGTH_KEY)
    return VIDEO_LENGTH_PRESETS[DEFAULT_LENGTH_KEY]


def get_style_directive(name: str | None) -> NarrativeStyleDirective:
    if not name:
        return NARRATIVE_STYLE_DIRECTIVES[DEFAULT_STYLE_KEY]
    key = _normalize_key(name)
    directive = NARRATIVE_STYLE_DIRECTIVES.get(key)
    if directive:
        return directive
    logger.warning("Unknown video_style '%s'; falling back to %s", name, DEFAULT_STYLE_KEY)
    return NARRATIVE_STYLE_DIRECTIVES[DEFAULT_STYLE_KEY]


def apply_profile_to_script(
    script: ScriptPlan,
    directive: NarrativeStyleDirective,
    profile: VideoLengthProfile,
) -> ScriptPlan:
    beats = list(script.beats)
    beats = _rebalance_beats(beats, profile)
    beats = _rescale_durations(beats, profile)
    return script.model_copy(
        update={
            "beats": beats,
            "target_runtime_sec": profile.target_runtime_sec,
            "target_beat_count": profile.target_beat_count,
            "narrative_style": directive.style_id,
        }
    )


def _rebalance_beats(beats: list[Beat], profile: VideoLengthProfile) -> list[Beat]:
    if not beats:
        return beats
    target = profile.target_beat_count
    if target <= 0:
        return beats
    work = list(beats)
    if len(work) > target:
        overflow = work[target:]
        work = work[:target]
        if overflow:
            combined = "\n\n".join(b.transcript.strip() for b in overflow if b.transcript.strip())
            if combined and work:
                last = work[-1]
                merged_text = "\n\n".join(filter(None, [last.transcript.strip(), combined]))
                work[-1] = last.model_copy(update={"transcript": merged_text})
            logger.info(
                "Trimmed %s overflow beats down to target %s for video length preset %s",
                len(overflow),
                target,
                profile.key,
            )
        return work

    split_token = 1
    stall_count = 0
    while len(work) < target and stall_count <= target:
        idx = _longest_transcript_index(work)
        if idx is None:
            break
        beat = work.pop(idx)
        split_result = _split_transcript(beat.transcript)
        if not split_result:
            work.insert(idx, beat)
            stall_count += 1
            continue
        first_text, second_text = split_result
        suffix = f"split{split_token}"
        split_token += 1
        first = beat.model_copy(update={"transcript": first_text})
        second_id = f"{beat.id}-{suffix}"
        second = beat.model_copy(update={"id": second_id, "transcript": second_text})
        work.insert(idx, second)
        work.insert(idx, first)
        stall_count = 0
    if len(work) < target:
        logger.warning(
            "Unable to reach %s beats (have %s). Consider regenerating script for %s",
            target,
            len(work),
            profile.key,
        )
    return work


def _rescale_durations(beats: list[Beat], profile: VideoLengthProfile) -> list[Beat]:
    if not beats:
        return beats
    durations = [_duration_or_fallback(beat, profile) for beat in beats]
    total = sum(durations)
    if total <= 0:
        total = profile.target_runtime_sec
        durations = [profile.target_runtime_sec / len(beats)] * len(beats)
    scale = profile.target_runtime_sec / total if total else 1.0
    scaled = [duration * scale for duration in durations]
    clamped = [
        max(profile.min_beat_duration_sec, min(profile.max_beat_duration_sec, value))
        for value in scaled
    ]
    adjusted_total = sum(clamped)
    delta = profile.target_runtime_sec - adjusted_total
    if abs(delta) > 0.01 and clamped:
        clamped[-1] = max(
            profile.min_beat_duration_sec,
            min(profile.max_beat_duration_sec, clamped[-1] + delta),
        )
    updated: list[Beat] = []
    for beat, duration in zip(beats, clamped):
        updated.append(beat.model_copy(update={"estimated_duration_sec": round(duration, 2)}))
    return updated


def _duration_or_fallback(beat: Beat, profile: VideoLengthProfile) -> float:
    if beat.estimated_duration_sec and beat.estimated_duration_sec > 0:
        return beat.estimated_duration_sec
    words = len(beat.transcript.split())
    if words:
        est = words / 2.5
    else:
        est = profile.default_beat_duration_sec
    return max(profile.min_beat_duration_sec, min(profile.max_beat_duration_sec, est))


def _split_transcript(text: str) -> tuple[str, str] | None:
    clean = text.strip()
    if not clean:
        return None
    sentences = [segment.strip() for segment in SENTENCE_BOUNDARY.split(clean) if segment.strip()]
    if len(sentences) >= 2:
        midpoint = len(sentences) // 2
        first = " ".join(sentences[:midpoint]).strip()
        second = " ".join(sentences[midpoint:]).strip()
        if first and second:
            return first, second
    midpoint = len(clean) // 2
    first = clean[:midpoint].strip()
    second = clean[midpoint:].strip()
    if first and second:
        return first, second
    return None


def _longest_transcript_index(beats: Sequence[Beat]) -> int | None:
    best_index: int | None = None
    best_length = -1
    for idx, beat in enumerate(beats):
        length = len(beat.transcript.split())
        if length > best_length:
            best_length = length
            best_index = idx
    return best_index


def _normalize_key(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.endswith("seconds"):
        normalized = normalized[:-7]
    normalized = normalized.replace("seconds", "").replace("sec", "")
    normalized = normalized.replace(" ", "")
    if normalized.isdigit():
        normalized = f"{normalized}s"
    normalized = normalized.replace("-", "_")
    return normalized
