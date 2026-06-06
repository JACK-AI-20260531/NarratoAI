from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class MovieClip:
    clip_id: str
    start_time: float
    end_time: float
    description: str = ""
    tags: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    emotion: str = ""
    scene_type: str = ""
    source_time_range: str = ""
    thumbnail_path: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    @property
    def searchable_text(self) -> str:
        parts = [
            self.description,
            self.emotion,
            self.scene_type,
            " ".join(self.tags),
            " ".join(self.characters),
        ]
        return " ".join(part for part in parts if part).lower()


@dataclass(slots=True)
class ScriptSegment:
    segment_id: str
    index: int
    text: str
    estimated_duration: float = 0.0
    keywords: list[str] = field(default_factory=list)
    emotion: str = ""
    visual_requirement: str = ""

    @property
    def searchable_text(self) -> str:
        parts = [self.text, self.emotion, self.visual_requirement, " ".join(self.keywords)]
        return " ".join(part for part in parts if part).lower()


@dataclass(slots=True)
class ClipCandidate:
    clip: MovieClip
    score: float
    start_time: float
    end_time: float
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TimelineItem:
    segment: ScriptSegment
    selected_clip: ClipCandidate | None
    candidates: list[ClipCandidate] = field(default_factory=list)
    locked: bool = False

    def to_video_clip_item(self) -> dict:
        if self.selected_clip is None:
            return {
                "_id": self.segment.index,
                "timestamp": format_time_range(0, max(1.0, self.segment.estimated_duration)),
                "picture": self.segment.visual_requirement or self.segment.text,
                "narration": self.segment.text,
                "OST": 2,
            }

        return {
            "_id": self.segment.index,
            "timestamp": format_time_range(self.selected_clip.start_time, self.selected_clip.end_time),
            "picture": self.selected_clip.clip.description or self.segment.visual_requirement or self.segment.text,
            "narration": self.segment.text,
            "OST": 2,
            "match_score": round(self.selected_clip.score, 4),
            "match_reasons": list(self.selected_clip.reasons),
            "source_clip_id": self.selected_clip.clip.clip_id,
        }


@dataclass(slots=True)
class ReferenceStyleProfile:
    opening_type: str = ""
    narrative_structure: str = ""
    tone: str = ""
    rhythm: str = ""
    hook_rules: list[str] = field(default_factory=list)
    sentence_patterns: list[str] = field(default_factory=list)
    originality_rules: list[str] = field(default_factory=list)
    prompt_template: str = ""


def format_timestamp(seconds: float) -> str:
    milliseconds_total = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds_total, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def format_time_range(start_time: float, end_time: float) -> str:
    return f"{format_timestamp(start_time)}-{format_timestamp(end_time)}"
