import asyncio
import threading
import time
import traceback
import uuid
from copy import deepcopy

import streamlit as st
from loguru import logger

from app.config import config
from app.services.movie_commentary import MovieMontageService, MovieSceneMatchingService

_TASKS: dict[str, dict] = {}
_TASK_LOCK = threading.Lock()


def match_movie_scenes(params):
    if not params.video_origin_path:
        st.error("请先选择原始电影文件")
        return

    script_text = st.session_state.get("movie_commentary_script", "")
    if not script_text:
        st.error("请先输入电影原片解说脚本")
        return

    task_config = _build_common_task_config(params.video_origin_path)
    task_config.update(
        {
            "script_text": script_text,
            "top_k": int(st.session_state.get("movie_match_top_k", 3) or 3),
        }
    )
    _start_background_task(
        task_key="movie_scene_match_task_id",
        task_type="commentary",
        task_config=task_config,
        worker=_run_movie_scene_matching_task,
        success_message="电影原片解说画面匹配任务已在后台启动，切换页面不会中断。",
        running_message="电影原片解说画面匹配任务正在后台运行，切换页面不会中断。",
    )


def build_movie_montage(params):
    if not params.video_origin_path:
        st.error("请先选择原始电影文件")
        return

    montage_theme = st.session_state.get("movie_montage_theme", "")
    if not montage_theme.strip():
        st.error("请先输入电影混剪主题或关键词")
        return

    task_config = _build_common_task_config(params.video_origin_path)
    task_config.update(
        {
            "montage_theme": montage_theme,
            "clip_count": int(st.session_state.get("movie_montage_clip_count", 8) or 8),
            "clip_duration": float(st.session_state.get("movie_montage_clip_duration", 3.0) or 3.0),
        }
    )
    _start_background_task(
        task_key="movie_montage_task_id",
        task_type="montage",
        task_config=task_config,
        worker=_run_movie_montage_task,
        success_message="电影混剪任务已在后台启动，切换页面不会中断。",
        running_message="电影混剪任务正在后台运行，切换页面不会中断。",
    )


def render_movie_scene_match_status():
    _render_task_status(
        task_key="movie_scene_match_task_id",
        running_label="电影原片解说画面匹配中",
        done_label="电影原片解说画面匹配完成",
        failed_label="电影原片解说画面匹配失败",
        refresh_key="refresh_movie_scene_match_status",
    )


def render_movie_montage_status():
    _render_task_status(
        task_key="movie_montage_task_id",
        running_label="电影混剪生成中",
        done_label="电影混剪生成完成",
        failed_label="电影混剪生成失败",
        refresh_key="refresh_movie_montage_status",
    )


def is_movie_scene_matching_running() -> bool:
    return _is_task_running("movie_scene_match_task_id")


def is_movie_montage_running() -> bool:
    return _is_task_running("movie_montage_task_id")


def _build_common_task_config(movie_path: str) -> dict:
    vision_llm_provider = (
        st.session_state.get("vision_llm_provider") or config.app.get("vision_llm_provider", "openai")
    ).lower()
    return {
        "movie_path": movie_path,
        "frame_interval_input": st.session_state.get("frame_interval_input")
        or config.frames.get("frame_interval_input", 3),
        "vision_llm_provider": vision_llm_provider,
        "vision_api_key": st.session_state.get(f"vision_{vision_llm_provider}_api_key")
        or config.app.get(f"vision_{vision_llm_provider}_api_key"),
        "vision_model_name": st.session_state.get(f"vision_{vision_llm_provider}_model_name")
        or config.app.get(f"vision_{vision_llm_provider}_model_name"),
        "vision_base_url": st.session_state.get(f"vision_{vision_llm_provider}_base_url")
        or config.app.get(f"vision_{vision_llm_provider}_base_url", ""),
        "vision_batch_size": st.session_state.get("vision_batch_size") or config.frames.get("vision_batch_size", 10),
        "max_concurrency": st.session_state.get("vision_max_concurrency")
        or config.frames.get("vision_max_concurrency", 2),
    }


def _start_background_task(
    *,
    task_key: str,
    task_type: str,
    task_config: dict,
    worker,
    success_message: str,
    running_message: str,
):
    current_task_id = st.session_state.get(task_key, "")
    if current_task_id and _get_task(current_task_id).get("status") == "running":
        st.info(running_message)
        return

    task_id = str(uuid.uuid4())
    with _TASK_LOCK:
        _TASKS[task_id] = {
            "type": task_type,
            "status": "running",
            "progress": 0,
            "message": "任务已启动，正在准备分析原片...",
            "result": None,
            "error": "",
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    st.session_state[task_key] = task_id

    thread = threading.Thread(
        target=worker,
        args=(task_id, task_config),
        daemon=True,
        name=f"movie-{task_type}-{task_id[:8]}",
    )
    thread.start()
    st.success(success_message)
    st.rerun()


def _render_task_status(*, task_key: str, running_label: str, done_label: str, failed_label: str, refresh_key: str):
    task_id = st.session_state.get(task_key, "")
    if not task_id:
        return

    task = _get_task(task_id)
    if not task:
        return

    status = task.get("status")
    progress = int(task.get("progress") or 0)
    message = task.get("message") or ""

    if status == "running":
        st.progress(progress)
        st.info(message or f"{running_label}：{progress}%")
        if st.button("刷新任务进度", key=refresh_key, use_container_width=True):
            st.rerun()
        return

    if status == "done":
        result = task.get("result") or {}
        st.session_state["video_clip_json"] = deepcopy(result.get("video_clip_json", []))
        st.success(f"{done_label}，共生成 {len(result.get('video_clip_json', []))} 个剪辑片段")
        _cleanup_task(task_id)
        st.session_state.pop(task_key, None)
        return

    if status == "failed":
        st.error(f"{failed_label}: {task.get('error') or message}")
        _cleanup_task(task_id)
        st.session_state.pop(task_key, None)


def _is_task_running(task_key: str) -> bool:
    task_id = st.session_state.get(task_key, "")
    return bool(task_id and _get_task(task_id).get("status") == "running")


def _run_movie_scene_matching_task(task_id: str, task_config: dict):
    def update_progress(progress: float, message: str = ""):
        _update_task_progress(task_id, progress, message)

    try:
        service = MovieSceneMatchingService()
        result = asyncio.run(
            service.analyze_movie_and_match(
                movie_path=task_config["movie_path"],
                script_text=task_config["script_text"],
                frame_interval_input=task_config["frame_interval_input"],
                top_k=task_config["top_k"],
                progress_callback=update_progress,
                vision_llm_provider=task_config["vision_llm_provider"],
                vision_api_key=task_config["vision_api_key"],
                vision_model_name=task_config["vision_model_name"],
                vision_base_url=task_config["vision_base_url"],
                vision_batch_size=task_config["vision_batch_size"],
                max_concurrency=task_config["max_concurrency"],
            )
        )
        _finish_task(task_id, result, "电影原片解说画面匹配完成")
    except Exception as err:
        _fail_task(task_id, err, "电影原片解说画面匹配失败")


def _run_movie_montage_task(task_id: str, task_config: dict):
    def update_progress(progress: float, message: str = ""):
        _update_task_progress(task_id, progress, message)

    try:
        service = MovieMontageService()
        result = asyncio.run(
            service.analyze_movie_and_build_montage(
                movie_path=task_config["movie_path"],
                montage_theme=task_config["montage_theme"],
                clip_count=task_config["clip_count"],
                clip_duration=task_config["clip_duration"],
                frame_interval_input=task_config["frame_interval_input"],
                progress_callback=update_progress,
                vision_llm_provider=task_config["vision_llm_provider"],
                vision_api_key=task_config["vision_api_key"],
                vision_model_name=task_config["vision_model_name"],
                vision_base_url=task_config["vision_base_url"],
                vision_batch_size=task_config["vision_batch_size"],
                max_concurrency=task_config["max_concurrency"],
            )
        )
        _finish_task(task_id, result, "电影混剪生成完成")
    except Exception as err:
        _fail_task(task_id, err, "电影混剪生成失败")


def _update_task_progress(task_id: str, progress: float, message: str = ""):
    with _TASK_LOCK:
        task = _TASKS.get(task_id)
        if not task:
            return
        task["progress"] = int(max(0, min(100, round(progress))))
        task["message"] = message or f"进度: {task['progress']}%"
        task["updated_at"] = time.time()


def _finish_task(task_id: str, result: dict, message: str):
    with _TASK_LOCK:
        task = _TASKS.get(task_id)
        if task:
            task["status"] = "done"
            task["progress"] = 100
            task["message"] = message
            task["result"] = result
            task["updated_at"] = time.time()


def _fail_task(task_id: str, err: Exception, message: str):
    logger.exception(f"{message}\n{traceback.format_exc()}")
    with _TASK_LOCK:
        task = _TASKS.get(task_id)
        if task:
            task["status"] = "failed"
            task["error"] = str(err)
            task["message"] = message
            task["updated_at"] = time.time()


def _get_task(task_id: str) -> dict:
    with _TASK_LOCK:
        return deepcopy(_TASKS.get(task_id, {}))


def _cleanup_task(task_id: str):
    with _TASK_LOCK:
        _TASKS.pop(task_id, None)
