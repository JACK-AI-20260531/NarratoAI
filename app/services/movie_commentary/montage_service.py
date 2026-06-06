from __future__ import annotations

from typing import Any

from app.services.movie_commentary.models import MovieClip, format_time_range
from app.services.movie_commentary.scene_matching_service import MovieSceneMatchingService


class MovieMontageService(MovieSceneMatchingService):
    async def analyze_movie_and_build_montage(
        self,
        *,
        movie_path: str,
        montage_theme: str,
        clip_count: int = 8,
        clip_duration: float = 3.0,
        frame_interval_input: int | float | None = None,
        progress_callback=None,
        **analysis_kwargs,
    ) -> dict[str, Any]:
        progress = progress_callback or (lambda _p, _m: None)
        progress(5, "正在分析原片画面...")
        analysis_result = await self.get_documentary_service().analyze_video(
            video_path=movie_path,
            frame_interval_input=frame_interval_input,
            progress_callback=progress_callback,
            custom_prompt=(
                "请为电影混剪挑选镜头做画面分析，重点识别：人物、动作、情绪、冲突、高能场面、"
                "视觉冲击、环境氛围、适合混剪的精彩瞬间。"
            ),
            **analysis_kwargs,
        )
        clips = self.build_clips_from_analysis(analysis_result.get("analysis_artifact", {}))
        selected_clips = self.select_montage_clips(
            clips,
            montage_theme=montage_theme,
            clip_count=clip_count,
            clip_duration=clip_duration,
        )
        return {
            "clips": clips,
            "selected_clips": selected_clips,
            "video_clip_json": selected_clips,
            "analysis_json_path": analysis_result.get("analysis_json_path", ""),
        }

    def select_montage_clips(
        self,
        clips: list[MovieClip],
        *,
        montage_theme: str,
        clip_count: int = 8,
        clip_duration: float = 3.0,
    ) -> list[dict]:
        theme_text = montage_theme.strip() or "高能精彩电影混剪"
        scored_clips = []
        for clip in clips:
            semantic_score = self.cosine_similarity(theme_text, clip.searchable_text)
            intensity_score = self.estimate_montage_intensity(clip.searchable_text)
            duration_score = self.duration_fit_score(clip_duration, clip.duration)
            score = semantic_score * 0.45 + intensity_score * 0.40 + duration_score * 0.15
            scored_clips.append((score, clip))

        scored_clips.sort(key=lambda item: item[0], reverse=True)
        selected = scored_clips[: max(1, clip_count)]
        selected.sort(key=lambda item: item[1].start_time)

        video_clip_json = []
        for index, (score, clip) in enumerate(selected, 1):
            start_time, end_time = self.choose_montage_range(clip, clip_duration)
            picture = clip.description or "电影混剪镜头"
            video_clip_json.append(
                {
                    "_id": index,
                    "timestamp": format_time_range(start_time, end_time),
                    "picture": picture,
                    "narration": f"电影混剪片段{index}：{picture}",
                    "OST": 1,
                    "match_score": round(score, 4),
                    "source_clip_id": clip.clip_id,
                    "montage_theme": theme_text,
                }
            )
        return video_clip_json

    def choose_montage_range(self, clip: MovieClip, clip_duration: float) -> tuple[float, float]:
        duration = max(1.0, clip_duration)
        if clip.duration <= duration:
            return clip.start_time, clip.end_time
        return clip.start_time, clip.start_time + duration

    def estimate_montage_intensity(self, text: str) -> float:
        high_intensity_words = [
            "追逐",
            "奔跑",
            "打斗",
            "爆炸",
            "冲突",
            "危险",
            "紧张",
            "反转",
            "高能",
            "震撼",
            "逃",
            "哭",
            "怒",
            "枪",
            "车",
            "火",
            "血",
        ]
        hit_count = sum(1 for word in high_intensity_words if word in (text or ""))
        return min(1.0, hit_count / 4)
