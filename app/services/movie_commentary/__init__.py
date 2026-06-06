from .models import MovieClip, ScriptSegment, ClipCandidate, TimelineItem, ReferenceStyleProfile

__all__ = [
    "MovieClip",
    "ScriptSegment",
    "ClipCandidate",
    "TimelineItem",
    "ReferenceStyleProfile",
    "MovieSceneMatchingService",
    "ReferencePromptService",
]


def __getattr__(name):
    if name == "MovieSceneMatchingService":
        from .scene_matching_service import MovieSceneMatchingService

        return MovieSceneMatchingService
    if name == "MovieMontageService":
        from .montage_service import MovieMontageService

        return MovieMontageService
    if name == "ReferencePromptService":
        from .reference_prompt_service import ReferencePromptService

        return ReferencePromptService
    raise AttributeError(name)
