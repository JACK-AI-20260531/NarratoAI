"""B站视频文案提取与脚本改写服务。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from loguru import logger

from app.config import config
from app.services.llm.unified_service import UnifiedLLMService


_BVID_RE = re.compile(r"\bBV[0-9A-Za-z]{10}\b")
_AVID_RE = re.compile(r"\bav(\d+)\b", re.IGNORECASE)
_SRT_TIME_RE = re.compile(r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})")


@dataclass(frozen=True)
class BilibiliSubtitle:
    title: str
    bvid: str
    cid: int
    transcript: str
    srt_content: str


class BilibiliRewriteError(Exception):
    """B站文案提取或脚本改写失败。"""


def _headers() -> dict[str, str]:
    """构建访问 B站公开接口的请求头。"""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
    }


def _normalize_bilibili_url(raw_url: str) -> str:
    """从用户粘贴的混合文本中提取可用的 B站链接或 BV/av 标识。"""
    raw_url = (raw_url or "").strip().strip("`'\"")
    if not raw_url:
        raise BilibiliRewriteError("请填写 B站视频链接")

    bvid_match = _BVID_RE.search(raw_url)
    if bvid_match:
        return f"https://www.bilibili.com/video/{bvid_match.group(0)}"

    avid_match = _AVID_RE.search(raw_url)
    if avid_match:
        return f"https://www.bilibili.com/video/av{avid_match.group(1)}"

    url_match = re.search(r"https?://[^\s`'\"，。！？；】]+", raw_url)
    if url_match:
        return url_match.group(0).rstrip("/.,;，。；】")

    return raw_url


def _extract_video_id(url: str) -> tuple[str, str]:
    """从 B站链接中提取 bvid 或 avid。"""
    url = _normalize_bilibili_url(url)

    bvid_match = _BVID_RE.search(url)
    if bvid_match:
        return "bvid", bvid_match.group(0)

    avid_match = _AVID_RE.search(url)
    if avid_match:
        return "aid", avid_match.group(1)

    query = parse_qs(urlparse(url).query)
    if query.get("bvid"):
        return "bvid", query["bvid"][0]
    if query.get("aid"):
        return "aid", query["aid"][0]

    raise BilibiliRewriteError("未识别到 B站 BV 号或 av 号")


def _seconds_to_srt_time(seconds: float) -> str:
    """将秒数格式化为 SRT 时间戳。"""
    milliseconds = max(0, int(round(float(seconds) * 1000)))
    hours = milliseconds // 3_600_000
    milliseconds %= 3_600_000
    minutes = milliseconds // 60_000
    milliseconds %= 60_000
    secs = milliseconds // 1000
    milliseconds %= 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _find_json_array_text(output: str) -> str:
    """从混杂文本中定位第一个完整 JSON 数组字符串。"""
    start = output.find("[")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(output)):
            char = output[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    return output[start:index + 1]
        start = output.find("[", start + 1)
    return ""


def _looks_like_script_item(item: dict[str, Any]) -> bool:
    """判断字典是否像一个视频脚本条目。"""
    keys = set(item.keys())
    return bool(
        {"timestamp", "时间戳", "time", "start", "开始时间"} & keys
        and {"narration", "解说", "文案", "text", "content", "内容"} & keys
    )


def _unwrap_script_array(parsed: Any) -> list[dict[str, Any]]:
    """从常见模型返回结构中递归取出脚本数组。"""
    if isinstance(parsed, list):
        dict_items = [item for item in parsed if isinstance(item, dict)]
        if dict_items:
            return dict_items
        raise BilibiliRewriteError("模型未返回脚本数组")

    if isinstance(parsed, dict):
        if _looks_like_script_item(parsed):
            return [parsed]

        candidate_keys = (
            "items", "item", "script", "scripts", "data", "result", "results", "segments", "clips",
            "video_clip_json", "video_script", "output", "content", "脚本", "视频脚本", "片段", "分段",
        )
        for key in candidate_keys:
            if key not in parsed:
                continue
            value = parsed.get(key)
            try:
                return _unwrap_script_array(value)
            except BilibiliRewriteError:
                pass

        for value in parsed.values():
            try:
                return _unwrap_script_array(value)
            except BilibiliRewriteError:
                pass

    raise BilibiliRewriteError("模型未返回脚本数组")


def _extract_json_array(output: str) -> list[dict[str, Any]]:
    """从模型输出中提取视频脚本 JSON 数组。"""
    output = (output or "").strip()
    output = re.sub(r"<think>[\s\S]*?</think>", "", output, flags=re.IGNORECASE).strip()
    output = re.sub(r"^<think>[\s\S]*?(?=```json|```|\[|\{)", "", output, flags=re.IGNORECASE).strip()

    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", output, re.IGNORECASE)
    if code_block:
        output = code_block.group(1).strip()

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        array_text = _find_json_array_text(output)
        if not array_text:
            object_match = re.search(r"\{[\s\S]*\}", output)
            if not object_match:
                raise
            parsed = json.loads(object_match.group(0))
        else:
            parsed = json.loads(array_text)

    return _unwrap_script_array(parsed)


def _value_from(item: dict[str, Any], *keys: str, default: Any = "") -> Any:
    """按多个候选字段名读取字典值。"""
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


def _normalize_timestamp(item: dict[str, Any]) -> str:
    """从脚本条目中标准化时间戳字段。"""
    timestamp = str(_value_from(item, "timestamp", "时间戳", "time", "time_range", "时间范围", default="")).strip()
    if not timestamp:
        start = _value_from(item, "start", "start_time", "begin", "开始", "开始时间", default="")
        end = _value_from(item, "end", "end_time", "finish", "结束", "结束时间", default="")
        if start not in (None, "") and end not in (None, ""):
            timestamp = f"{start}-{end}"
    return timestamp.replace(" --> ", "-").replace(".", ",")


def _normalize_script_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """标准化脚本条目字段，确保兼容后续视频生成。"""
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        timestamp = _normalize_timestamp(item)
        if "-" not in timestamp:
            continue

        narration = str(_value_from(item, "narration", "解说", "文案", "text", "content", "内容", default="")).strip()
        picture = str(_value_from(item, "picture", "画面", "scene", "description", "描述", default=narration)).strip()
        normalized.append({
            "_id": int(_value_from(item, "_id", "id", "序号", default=index) or index),
            "timestamp": timestamp,
            "picture": picture,
            "narration": narration,
            "OST": int(_value_from(item, "OST", "ost", "原声", default=0) or 0),
        })

    if not normalized:
        raise BilibiliRewriteError("模型返回脚本为空或缺少有效时间戳")
    return normalized


def fetch_bilibili_subtitle(url: str) -> BilibiliSubtitle:
    """根据 B站链接提取公开视频字幕文案。"""
    id_type, video_id = _extract_video_id(url)
    params = {id_type: video_id}
    view_response = requests.get(
        "https://api.bilibili.com/x/web-interface/view",
        params=params,
        headers=_headers(),
        timeout=30,
    )
    view_response.raise_for_status()
    view_data = view_response.json()
    if view_data.get("code") != 0:
        raise BilibiliRewriteError(f"获取视频信息失败: {view_data.get('message')}")

    video_data = view_data.get("data") or {}
    pages = video_data.get("pages") or []
    if not pages:
        raise BilibiliRewriteError("未获取到视频分 P 信息")

    bvid = video_data.get("bvid") or video_id
    cid = int(pages[0].get("cid"))
    title = video_data.get("title") or bvid

    player_response = requests.get(
        "https://api.bilibili.com/x/player/v2",
        params={"bvid": bvid, "cid": cid},
        headers=_headers(),
        timeout=30,
    )
    player_response.raise_for_status()
    player_data = player_response.json()
    if player_data.get("code") != 0:
        raise BilibiliRewriteError(f"获取字幕列表失败: {player_data.get('message')}")

    subtitle_list = (((player_data.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
    if not subtitle_list:
        raise BilibiliRewriteError("该视频没有公开字幕，无法直接提取文案")

    subtitle_url = subtitle_list[0].get("subtitle_url") or ""
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    if not subtitle_url:
        raise BilibiliRewriteError("字幕地址为空")

    subtitle_response = requests.get(subtitle_url, headers=_headers(), timeout=30)
    subtitle_response.raise_for_status()
    body = subtitle_response.json().get("body") or []
    if not body:
        raise BilibiliRewriteError("字幕内容为空")

    transcript_lines: list[str] = []
    srt_lines: list[str] = []
    for index, item in enumerate(body, start=1):
        start = float(item.get("from", 0))
        end = float(item.get("to", start + 1))
        text = str(item.get("content") or "").strip()
        if not text:
            continue
        transcript_lines.append(f"[{_seconds_to_srt_time(start)}-{_seconds_to_srt_time(end)}] {text}")
        srt_lines.extend([
            str(index),
            f"{_seconds_to_srt_time(start)} --> {_seconds_to_srt_time(end)}",
            text,
            "",
        ])

    return BilibiliSubtitle(
        title=title,
        bvid=bvid,
        cid=cid,
        transcript="\n".join(transcript_lines),
        srt_content="\n".join(srt_lines).strip(),
    )


def download_bilibili_audio(url: str, output_dir: str, cookie: str = "", cookies_from_browser: str = "") -> str:
    """使用 yt-dlp 下载 B站视频音频，返回本地音频文件路径。"""
    url = _normalize_bilibili_url(url)
    os.makedirs(output_dir, exist_ok=True)
    file_prefix = f"bilibili_audio_{int(time.time())}"
    output_template = os.path.join(output_dir, f"{file_prefix}.%(ext)s")
    request_headers = _headers()
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--add-header",
        f"User-Agent: {request_headers['User-Agent']}",
        "--add-header",
        f"Referer: {request_headers['Referer']}",
    ]
    if cookie.strip():
        cmd.extend(["--add-header", f"Cookie: {cookie.strip()}"])
    if cookies_from_browser.strip():
        cmd.extend(["--cookies-from-browser", cookies_from_browser.strip()])
    cmd.extend([
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        output_template,
        url,
    ])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=True)
    except FileNotFoundError as exc:
        raise BilibiliRewriteError("未找到 yt-dlp，请先安装依赖: pip install yt-dlp") from exc
    except subprocess.CalledProcessError as exc:
        error_text = (exc.stderr or exc.stdout or "").strip()
        if "HTTP Error 412" in error_text or "Precondition Failed" in error_text:
            raise BilibiliRewriteError("B站音频下载被风控拦截（HTTP 412），请在 B站文案改写区域选择已登录的浏览器登录态，或手动填写 Cookie 后重试") from exc
        raise BilibiliRewriteError(f"B站音频下载失败: {error_text[:500]}") from exc

    downloaded_files = [
        os.path.join(output_dir, name)
        for name in os.listdir(output_dir)
        if name.startswith(file_prefix) and name.lower().endswith((".mp3", ".m4a", ".wav", ".webm"))
    ]
    if not downloaded_files:
        raise BilibiliRewriteError(f"B站音频下载完成但未找到输出文件: {result.stdout[-500:]}")
    return max(downloaded_files, key=os.path.getmtime)


def _build_rewrite_prompt(title: str, transcript: str, clip_count: int, user_prompt: str = "") -> str:
    """构建 B站文案改写为视频脚本的提示词。"""
    custom_instruction = (user_prompt or "").strip()
    custom_section = f"\n用户改写要求：\n{custom_instruction}\n" if custom_instruction else ""
    return f"""
你是一名专业短视频脚本改写师。请根据下面从 B站视频提取的字幕文案，改写成 NarratoAI 可用的视频脚本 JSON 数组。
{custom_section}
基础要求：
1. 只返回 JSON 数组本身，最外层必须是 []，不要返回对象，不要输出解释、Markdown、代码块或思考过程。
2. 数组长度尽量接近 {clip_count} 段。
3. 每段必须包含字段：_id、timestamp、picture、narration、OST。
4. timestamp 必须使用原字幕中的时间范围，格式为 HH:MM:SS,mmm-HH:MM:SS,mmm。
5. picture 写这一段画面/内容概述。
6. narration 写改写后的中文解说文案，适合直接配音。
7. OST 使用 0，表示使用解说配音。

视频标题：{title}

字幕文案：
{transcript}
""".strip()


async def _rewrite_with_llm(title: str, transcript: str, clip_count: int, temperature: float, user_prompt: str = "") -> list[dict[str, Any]]:
    """调用当前文本模型改写 B站文案为视频脚本。"""
    provider = config.app.get("text_llm_provider", "gemini").lower()
    prompt = _build_rewrite_prompt(title, transcript, clip_count, user_prompt)
    result = await UnifiedLLMService.generate_text(
        prompt=prompt,
        system_prompt="你是一名专业短视频脚本改写师，只输出合法 JSON。",
        provider=provider,
        temperature=temperature,
        response_format="json",
    )
    logger.debug(f"B站文案改写模型原始输出: {str(result)[:2000]}")
    return _normalize_script_items(_extract_json_array(result))


def rewrite_transcript_to_script(
    title: str,
    transcript: str,
    clip_count: int = 8,
    temperature: float = 0.7,
    user_prompt: str = "",
) -> list[dict[str, Any]]:
    """将已提取或已编辑的 B站文案改写为视频脚本。"""
    if not transcript or not transcript.strip():
        raise BilibiliRewriteError("请先提取或填写 B站文案")
    try:
        return asyncio.run(_rewrite_with_llm(title, transcript, clip_count, temperature, user_prompt))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_rewrite_with_llm(title, transcript, clip_count, temperature, user_prompt))
        finally:
            loop.close()


def rewrite_bilibili_to_script(
    url: str,
    clip_count: int = 8,
    temperature: float = 0.7,
    user_prompt: str = "",
) -> tuple[BilibiliSubtitle, list[dict[str, Any]]]:
    """提取 B站字幕文案并改写为视频脚本。"""
    subtitle = fetch_bilibili_subtitle(url)
    script = rewrite_transcript_to_script(subtitle.title, subtitle.transcript, clip_count, temperature, user_prompt)
    logger.info(f"B站文案改写完成: {subtitle.bvid}, 脚本段数: {len(script)}")
    return subtitle, script
