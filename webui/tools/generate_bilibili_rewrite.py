import json
import os
import time
import traceback

import streamlit as st
from loguru import logger

from app.config import config
from app.services import fun_asr_subtitle, third_party_asr_subtitle
from app.services.bilibili_script import (
    BilibiliRewriteError,
    download_bilibili_audio,
    fetch_bilibili_subtitle,
    rewrite_transcript_to_script,
)
from app.utils import utils


def _safe_filename_part(value: str, default: str = "bilibili") -> str:
    """生成适合保存到本地的文件名片段。"""
    import re

    name = str(value or "").strip()
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("._")
    return name[:80] or default


def _srt_to_transcript(srt_content: str) -> str:
    """将 SRT 字幕内容转换为带时间戳的文案文本。"""
    import re

    blocks = re.split(r"\n\s*\n", (srt_content or "").strip())
    transcript_lines: list[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        time_line = next((line for line in lines if "-->" in line), "")
        if not time_line:
            continue
        text_lines = [line for line in lines if line != time_line and not line.isdigit()]
        text = "".join(text_lines).strip()
        if text:
            timestamp = re.sub(r"\s*-->\s*", "-", time_line).replace(".", ",")
            transcript_lines.append(f"[{timestamp}] {text}")
    return "\n".join(transcript_lines)


def _transcribe_audio_with_default_asr(audio_path: str, subtitle_path: str) -> str:
    """使用当前默认 ASR 配置转写本地音频并返回 SRT 路径。"""
    if config.third_party_asr.get("is_default", False):
        return third_party_asr_subtitle.create_with_third_party_asr(
            local_file=audio_path,
            subtitle_file=subtitle_path,
            api_url=config.third_party_asr.get("api_url", ""),
            api_key=config.third_party_asr.get("api_key", ""),
            model=config.third_party_asr.get("model", ""),
            response_format=config.third_party_asr.get("response_format", "srt"),
        )
    return fun_asr_subtitle.create_with_fun_asr(
        local_file=audio_path,
        subtitle_file=subtitle_path,
        api_key=config.fun_asr.get("api_key", ""),
    )


def _ensure_llm_providers_registered() -> None:
    """确保文本模型提供商已注册，避免脚本改写时注册表为空。"""
    from app.services.llm.manager import LLMServiceManager
    from app.services.llm.providers import register_all_providers

    if not LLMServiceManager.is_registered():
        register_all_providers()
        st.session_state['llm_providers_registered'] = True


def _save_bilibili_text(title: str, bvid: str, transcript: str) -> str:
    """保存 B站提取文案到脚本目录，便于后续编辑和复用。"""
    timestamp = time.strftime("%Y%m%d%H%M%S")
    base_name = _safe_filename_part(f"{title}_{bvid}")
    text_path = os.path.join(utils.script_dir(), f"{base_name}_{timestamp}_bilibili_transcript.txt")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(transcript)
    return text_path


def _save_bilibili_srt(title: str, bvid: str, srt_content: str) -> str:
    """保存 B站字幕为 SRT 文件。"""
    timestamp = time.strftime("%Y%m%d%H%M%S")
    base_name = _safe_filename_part(f"{title}_{bvid}")
    subtitle_path = os.path.join(utils.subtitle_dir(), f"{base_name}_{timestamp}_bilibili.srt")
    with open(subtitle_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    return subtitle_path


def _save_rewrite_script(title: str, bvid: str, script: list[dict]) -> str:
    """保存 B站文案改写后的视频脚本 JSON。"""
    timestamp = time.strftime("%Y%m%d%H%M%S")
    base_name = _safe_filename_part(f"{title}_{bvid}")
    script_path = os.path.join(utils.script_dir(), f"{base_name}_{timestamp}_bilibili_rewrite.json")
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    return script_path


def extract_bilibili_transcript(url: str, cookie: str = "", cookies_from_browser: str = ""):
    """从 B站链接提取字幕文案；无公开字幕时下载音频并调用默认 ASR。"""
    progress_bar = st.progress(0)
    status_text = st.empty()

    try:
        if not url or not url.strip():
            st.error("请先填写 B站视频链接")
            return

        progress_bar.progress(15)
        status_text.text("15% - 正在尝试提取 B站公开字幕")
        transcript_source = "公开字幕"
        try:
            with st.spinner("正在提取 B站公开字幕文案..."):
                subtitle = fetch_bilibili_subtitle(url.strip())
            text_path = _save_bilibili_text(subtitle.title, subtitle.bvid, subtitle.transcript)
            subtitle_path = _save_bilibili_srt(subtitle.title, subtitle.bvid, subtitle.srt_content)
            transcript = subtitle.transcript
            srt_content = subtitle.srt_content
            title = subtitle.title
            bvid = subtitle.bvid
        except BilibiliRewriteError as subtitle_error:
            logger.warning(f"B站公开字幕提取失败，尝试音频 ASR 兜底: {subtitle_error}")
            transcript_source = "音频 ASR"
            progress_bar.progress(35)
            status_text.text("35% - 未找到公开字幕，正在下载音频")
            audio_dir = utils.storage_dir("temp", create=True)
            audio_dir = os.path.join(audio_dir, "bilibili_audio")
            with st.spinner("未找到公开字幕，正在下载音频..."):
                audio_path = download_bilibili_audio(
                    url.strip(),
                    audio_dir,
                    cookie=cookie,
                    cookies_from_browser=cookies_from_browser,
                )

            progress_bar.progress(65)
            status_text.text("65% - 正在调用默认 ASR 转写音频")
            title = "B站音频转写"
            bvid = "bilibili_asr"
            subtitle_path = os.path.join(utils.subtitle_dir(), f"{_safe_filename_part(bvid)}_{int(time.time())}_bilibili_asr.srt")
            with st.spinner("正在调用默认 ASR 转写音频..."):
                subtitle_path = _transcribe_audio_with_default_asr(audio_path, subtitle_path)
            with open(subtitle_path, "r", encoding="utf-8") as f:
                srt_content = f.read()
            transcript = _srt_to_transcript(srt_content)
            if not transcript.strip():
                raise BilibiliRewriteError("ASR 已生成字幕，但未解析到可用文案")
            text_path = _save_bilibili_text(title, bvid, transcript)

        st.session_state["bilibili_title"] = title
        st.session_state["bilibili_bvid"] = bvid
        st.session_state["bilibili_transcript"] = transcript
        st.session_state["bilibili_transcript_path"] = text_path
        st.session_state["bilibili_transcript_source"] = transcript_source
        st.session_state["subtitle_path"] = subtitle_path
        st.session_state["subtitle_content"] = srt_content
        st.session_state["subtitle_file_processed"] = True
        config.ui["subtitle_path"] = subtitle_path
        config.save_config()

        progress_bar.progress(100)
        status_text.text(f"100% - B站文案提取完成（来源：{transcript_source}）")
        st.success(f"B站文案已保存: {os.path.basename(text_path)}")
        time.sleep(0.3)
        st.rerun()
    except BilibiliRewriteError as e:
        progress_bar.progress(100)
        st.error(str(e))
        st.info("如果仍失败，请查看终端里的“B站文案改写模型原始输出”日志，确认模型实际返回格式。")
    except Exception as e:
        progress_bar.progress(100)
        logger.error(f"B站文案提取失败: {traceback.format_exc()}")
        st.error(f"B站文案提取失败: {str(e)}")


def rewrite_bilibili_transcript_script(
    transcript: str,
    user_prompt: str,
    clip_count: int = 8,
    temperature: float = 0.7,
):
    """使用已提取/已编辑的 B站文案和用户提示词改写脚本。"""
    progress_bar = st.progress(0)
    status_text = st.empty()

    try:
        if not transcript or not transcript.strip():
            st.error("请先提取或填写 B站文案")
            return

        title = st.session_state.get("bilibili_title", "B站文案")
        bvid = st.session_state.get("bilibili_bvid", "bilibili")

        progress_bar.progress(30)
        status_text.text("30% - 正在按提示词改写脚本")
        _ensure_llm_providers_registered()
        with st.spinner("正在按你的提示词改写脚本..."):
            script = rewrite_transcript_to_script(
                title=title,
                transcript=transcript.strip(),
                clip_count=clip_count,
                temperature=temperature,
                user_prompt=user_prompt,
            )

        script_path = _save_rewrite_script(title, bvid, script)
        st.session_state["bilibili_transcript"] = transcript.strip()
        st.session_state["video_clip_json"] = script
        st.session_state["video_clip_json_path"] = script_path
        st.session_state["_switch_to_file_mode"] = True

        progress_bar.progress(100)
        status_text.text("100% - B站文案改写完成")
        st.success(f"B站文案改写完成: {os.path.basename(script_path)}")
        time.sleep(0.3)
        st.rerun()
    except BilibiliRewriteError as e:
        progress_bar.progress(100)
        st.error(str(e))
        st.info("如果仍失败，请查看终端里的“B站文案改写模型原始输出”日志，确认模型实际返回格式。")
    except Exception as e:
        progress_bar.progress(100)
        logger.error(f"B站文案改写失败: {traceback.format_exc()}")
        st.error(f"B站文案改写失败: {str(e)}")
