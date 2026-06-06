from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from app.services.documentary.frame_analysis_service import DocumentaryFrameAnalysisService
from app.services.movie_commentary.models import ClipCandidate, MovieClip, ScriptSegment, TimelineItem


class MovieSceneMatchingService:
    def __init__(self, documentary_service=None):
        self.documentary_service = documentary_service

    def get_documentary_service(self):
        if self.documentary_service is None:
            from app.services.documentary.frame_analysis_service import DocumentaryFrameAnalysisService

            self.documentary_service = DocumentaryFrameAnalysisService()
        return self.documentary_service

    async def analyze_movie_and_match(
        self,
        *,
        movie_path: str,
        script_text: str,
        frame_interval_input: int | float | None = None,
        top_k: int = 3,
        progress_callback=None,
        **analysis_kwargs,
    ) -> dict[str, Any]:
        progress = progress_callback or (lambda _p, _m: None)
        progress(5, "正在分析原片画面...")
        analysis_result = await self.get_documentary_service().analyze_video(
            video_path=movie_path,
            frame_interval_input=frame_interval_input,
            progress_callback=progress_callback,
            custom_prompt="请重点识别电影解说匹配所需信息：人物、场景、动作、情绪、关键剧情、画面氛围。",
            **analysis_kwargs,
        )
        clips = self.build_clips_from_analysis(analysis_result.get("analysis_artifact", {}))
        segments = self.split_script(script_text)
        timeline = self.match_segments(segments, clips, top_k=top_k)
        return {
            "segments": segments,
            "clips": clips,
            "timeline": timeline,
            "video_clip_json": [item.to_video_clip_item() for item in timeline],
            "analysis_json_path": analysis_result.get("analysis_json_path", ""),
        }

    def build_clips_from_analysis(self, analysis_artifact: dict[str, Any]) -> list[MovieClip]:
        clips: list[MovieClip] = []
        batches = analysis_artifact.get("batches") or []
        for index, batch in enumerate(batches):
            time_range = str(batch.get("time_range", "") or "")
            start_time, end_time = self.parse_time_range(time_range)
            observations = batch.get("frame_observations") or []
            observation_text = " ".join(
                DocumentaryFrameAnalysisService.sanitize_visual_description(str(item.get("observation", "")))
                for item in observations
                if isinstance(item, dict)
            )
            raw_description = str(batch.get("overall_activity_summary") or batch.get("fallback_summary") or observation_text).strip()
            description = DocumentaryFrameAnalysisService.sanitize_visual_description(raw_description)
            if not description:
                description = DocumentaryFrameAnalysisService.sanitize_visual_description(observation_text)
            if not description:
                description = "该片段暂无可用画面描述。"
            frame_paths = batch.get("frame_paths") or []
            tags = self.extract_keywords(f"{description} {observation_text}", limit=12)
            clips.append(
                MovieClip(
                    clip_id=f"movie_clip_{index + 1:04d}",
                    start_time=start_time,
                    end_time=max(end_time, start_time + 1.0),
                    description=description,
                    tags=tags,
                    emotion=self.detect_emotion(description),
                    scene_type=self.detect_scene_type(description),
                    source_time_range=time_range,
                    thumbnail_path=str(frame_paths[0]) if frame_paths else "",
                )
            )
        return clips

    def split_script(self, script_text: str, *, max_chars: int = 48) -> list[ScriptSegment]:
        text = (script_text or "").strip()
        if not text:
            return []

        raw_parts = [part.strip() for part in re.split(r"[\n]+|(?<=[。！？!?；;])", text) if part.strip()]
        parts: list[str] = []
        for raw_part in raw_parts:
            if len(raw_part) <= max_chars:
                parts.append(raw_part)
                continue
            buffer = ""
            for chunk in re.split(r"(?<=[，,、])", raw_part):
                if len(buffer) + len(chunk) > max_chars and buffer:
                    parts.append(buffer.strip())
                    buffer = chunk
                else:
                    buffer += chunk
            if buffer.strip():
                parts.append(buffer.strip())

        segments: list[ScriptSegment] = []
        for index, part in enumerate(parts, 1):
            keywords = self.extract_keywords(part, limit=8)
            segments.append(
                ScriptSegment(
                    segment_id=f"script_segment_{index:04d}",
                    index=index,
                    text=part,
                    estimated_duration=self.estimate_narration_duration(part),
                    keywords=keywords,
                    emotion=self.detect_emotion(part),
                    visual_requirement=part,
                )
            )
        return segments

    def match_segments(self, segments: list[ScriptSegment], clips: list[MovieClip], *, top_k: int = 3) -> list[TimelineItem]:
        timeline: list[TimelineItem] = []
        previous_clip_start = -math.inf
        used_counts: Counter[str] = Counter()

        for segment in segments:
            candidates = [
                self.score_candidate(segment, clip, previous_clip_start, used_counts[clip.clip_id])
                for clip in clips
            ]
            candidates.sort(key=lambda item: item.score, reverse=True)
            selected_candidates = candidates[: max(1, top_k)]
            selected = selected_candidates[0] if selected_candidates else None
            if selected:
                previous_clip_start = selected.clip.start_time
                used_counts[selected.clip.clip_id] += 1
            timeline.append(TimelineItem(segment=segment, selected_clip=selected, candidates=selected_candidates))
        return timeline

    def score_candidate(
        self,
        segment: ScriptSegment,
        clip: MovieClip,
        previous_clip_start: float,
        used_count: int,
    ) -> ClipCandidate:
        reasons: list[str] = []
        segment_terms = set(self.tokenize(segment.searchable_text))
        clip_terms = set(self.tokenize(clip.searchable_text))
        overlap = segment_terms & clip_terms

        keyword_score = len(overlap) / max(1, len(segment_terms))
        if overlap:
            reasons.append("关键词匹配：" + "、".join(sorted(overlap)[:6]))

        emotion_score = 1.0 if segment.emotion and segment.emotion == clip.emotion else 0.0
        if emotion_score:
            reasons.append(f"情绪匹配：{clip.emotion}")

        scene_score = 1.0 if clip.scene_type and clip.scene_type in segment.searchable_text else 0.0
        if scene_score:
            reasons.append(f"场景匹配：{clip.scene_type}")

        semantic_score = self.cosine_similarity(segment.searchable_text, clip.searchable_text)
        if semantic_score >= 0.1:
            reasons.append("语义描述相近")

        duration_score = self.duration_fit_score(segment.estimated_duration, clip.duration)
        order_score = 1.0 if clip.start_time >= previous_clip_start else 0.25
        reuse_score = max(0.2, 1.0 - used_count * 0.25)

        score = (
            keyword_score * 0.34
            + semantic_score * 0.28
            + emotion_score * 0.12
            + scene_score * 0.10
            + duration_score * 0.08
            + order_score * 0.05
            + reuse_score * 0.03
        )

        start_time, end_time = self.choose_clip_range(clip, segment.estimated_duration, used_count)
        if not reasons:
            reasons.append("未找到强匹配，按时间线和时长兜底推荐")
        return ClipCandidate(clip=clip, score=score, start_time=start_time, end_time=end_time, reasons=reasons)

    def choose_clip_range(self, clip: MovieClip, target_duration: float, used_count: int) -> tuple[float, float]:
        duration = max(1.0, target_duration or min(clip.duration, 5.0))
        if clip.duration <= duration:
            return clip.start_time, clip.end_time
        available = max(0.0, clip.duration - duration)
        offset = min(available, used_count * duration)
        return clip.start_time + offset, clip.start_time + offset + duration

    def duration_fit_score(self, target_duration: float, clip_duration: float) -> float:
        if target_duration <= 0 or clip_duration <= 0:
            return 0.5
        if clip_duration >= target_duration:
            return min(1.0, target_duration / max(target_duration, min(clip_duration, target_duration * 2)))
        return max(0.0, clip_duration / target_duration)

    def estimate_narration_duration(self, text: str) -> float:
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
        other_tokens = len(re.findall(r"[A-Za-z0-9]+", text or ""))
        return max(2.0, chinese_chars / 4.2 + other_tokens / 2.6)

    def extract_keywords(self, text: str, *, limit: int = 10) -> list[str]:
        tokens = self.tokenize(text)
        stop_words = {"一个", "这个", "那个", "然后", "就是", "开始", "发现", "自己", "他们", "我们", "画面", "视频"}
        counts = Counter(token for token in tokens if token not in stop_words and len(token.strip()) >= 2)
        return [token for token, _count in counts.most_common(limit)]

    def tokenize(self, text: str) -> list[str]:
        text = (text or "").lower()
        chinese_tokens = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        english_tokens = re.findall(r"[a-z0-9]{2,}", text)
        short_chinese = []
        for token in chinese_tokens:
            if len(token) <= 4:
                short_chinese.append(token)
            else:
                short_chinese.extend(token[index : index + 2] for index in range(0, len(token) - 1))
        return short_chinese + english_tokens

    def cosine_similarity(self, left: str, right: str) -> float:
        left_counts = Counter(self.tokenize(left))
        right_counts = Counter(self.tokenize(right))
        if not left_counts or not right_counts:
            return 0.0
        common = set(left_counts) & set(right_counts)
        dot = sum(left_counts[token] * right_counts[token] for token in common)
        left_norm = math.sqrt(sum(value * value for value in left_counts.values()))
        right_norm = math.sqrt(sum(value * value for value in right_counts.values()))
        return dot / max(1e-9, left_norm * right_norm)

    def detect_emotion(self, text: str) -> str:
        emotion_words = {
            "紧张": ["紧张", "危险", "恐惧", "害怕", "追杀", "逃", "惊悚", "压迫"],
            "悲伤": ["悲伤", "痛苦", "哭", "离别", "绝望", "牺牲"],
            "温暖": ["温暖", "治愈", "微笑", "拥抱", "家", "希望"],
            "愤怒": ["愤怒", "怒", "报仇", "冲突", "争吵"],
            "悬疑": ["秘密", "真相", "疑点", "线索", "反转", "谜"],
        }
        for emotion, words in emotion_words.items():
            if any(word in (text or "") for word in words):
                return emotion
        return ""

    def detect_scene_type(self, text: str) -> str:
        scene_words = {
            "室内": ["房间", "室内", "客厅", "卧室", "办公室", "走廊"],
            "城市": ["城市", "街道", "大楼", "车流", "地铁"],
            "荒野": ["森林", "荒野", "山", "河", "雪地"],
            "学校": ["学校", "教室", "操场"],
            "医院": ["医院", "病房", "医生"],
        }
        for scene, words in scene_words.items():
            if any(word in (text or "") for word in words):
                return scene
        return ""

    def parse_time_range(self, time_range: str) -> tuple[float, float]:
        if "-" not in (time_range or ""):
            return 0.0, 0.0
        start, end = time_range.split("-", 1)
        return self.parse_timestamp(start), self.parse_timestamp(end)

    def parse_timestamp(self, timestamp: str) -> float:
        text = (timestamp or "").strip()
        if not text:
            return 0.0
        try:
            if "," in text:
                time_part, ms_part = text.split(",", 1)
                milliseconds = int(ms_part[:3].ljust(3, "0"))
            else:
                time_part = text
                milliseconds = 0
            parts = [int(part) for part in time_part.split(":") if part]
            while len(parts) < 3:
                parts.insert(0, 0)
            hours, minutes, seconds = parts[-3], parts[-2], parts[-1]
            return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000
        except Exception:
            return 0.0
