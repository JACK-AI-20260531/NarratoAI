"""自定义 ASR 字幕转录服务。"""

from __future__ import annotations

import os
import time
from typing import Any

import requests
from loguru import logger

from app.utils import utils


class ThirdPartyAsrError(RuntimeError):
    """自定义 ASR 转录失败时抛出的异常。"""


PUNCTUATION_BREAKS = set("，。！？；,.!?;")


def _require_api_url(api_url: str) -> str:
    """校验并返回 ASR 转录接口地址。"""
    api_url = (api_url or "").strip().rstrip("/")
    if not api_url:
        raise ThirdPartyAsrError("请先输入 ASR API URL")
    if not (api_url.startswith("http://") or api_url.startswith("https://")):
        raise ThirdPartyAsrError("ASR API URL 必须以 http:// 或 https:// 开头")
    if api_url.endswith("/v1"):
        return f"{api_url}/audio/transcriptions"
    return api_url


def _normalize_response_format(response_format: str) -> str:
    """标准化 ASR 响应格式，默认请求 SRT。"""
    response_format = (response_format or "srt").strip().lower()
    allowed_formats = {"srt", "json", "text", "verbose_json"}
    return response_format if response_format in allowed_formats else "srt"


def _auth_headers(api_key: str) -> dict[str, str]:
    """根据 API Key 构建请求头。"""
    api_key = (api_key or "").strip()
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def _ms_to_srt_time(ms: float) -> str:
    """将毫秒时间戳转换为 SRT 时间格式。"""
    total_ms = max(0, int(round(float(ms))))
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    seconds = total_ms // 1_000
    milliseconds = total_ms % 1_000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _to_milliseconds(value: Any) -> float:
    """将秒或毫秒时间戳统一转换为毫秒。"""
    number = float(value)
    if number < 10_000:
        return number * 1000
    return number


def _srt_block(index: int, start_ms: float, end_ms: float, text: str) -> str:
    """构建单条 SRT 字幕块。"""
    if end_ms <= start_ms:
        end_ms = start_ms + 500
    return f"{index}\n{_ms_to_srt_time(start_ms)} --> {_ms_to_srt_time(end_ms)}\n{text.strip()}\n"


def _split_text(text: str, max_chars: int) -> list[str]:
    """按标点和最大长度拆分纯文本。"""
    chunks: list[str] = []
    current = ""
    for char in text:
        current += char
        if char in PUNCTUATION_BREAKS or len(current) >= max_chars:
            chunks.append(current.strip())
            current = ""
    if current.strip():
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk]


def _segments_to_srt(segments: list[dict[str, Any]]) -> str:
    """将通用 segments 结果转换为 SRT。"""
    lines = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text") or segment.get("content") or "").strip()
        if not text:
            continue
        start = segment.get("start", segment.get("start_time", segment.get("begin_time", 0)))
        end = segment.get("end", segment.get("end_time", segment.get("finish_time")))
        if end is None:
            end = _to_milliseconds(start) + max(1200, len(text) * 180)
        lines.append(_srt_block(index, _to_milliseconds(start), _to_milliseconds(end), text))
    if not lines:
        raise ThirdPartyAsrError("ASR 返回结果中没有可用字幕片段")
    return "\n".join(lines).rstrip() + "\n"


def _plain_text_to_srt(text: str, max_chars: int = 20) -> str:
    """将无时间戳纯文本转换为粗略 SRT。"""
    chunks = _split_text(text, max_chars)
    if not chunks:
        raise ThirdPartyAsrError("ASR 返回的文本为空")
    lines = []
    cursor = 0.0
    for index, chunk in enumerate(chunks, start=1):
        duration = max(1200.0, len(chunk) * 180.0)
        lines.append(_srt_block(index, cursor, cursor + duration, chunk))
        cursor += duration
    return "\n".join(lines).rstrip() + "\n"


def parse_asr_response_to_srt(response: requests.Response) -> str:
    """解析 ASR 响应并转换为 SRT。"""
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        response_text = response.text[:500] if response.text else ""
        raise ThirdPartyAsrError(
            f"ASR 请求失败: HTTP {response.status_code}, url={response.url}, response={response_text}"
        ) from exc
    content_type = (response.headers.get("Content-Type") or "").lower()
    text = response.text.strip()
    if "-->" in text:
        return text.rstrip() + "\n"
    if "json" not in content_type:
        return _plain_text_to_srt(text)

    data = response.json()
    if not isinstance(data, dict):
        raise ThirdPartyAsrError("ASR 返回 JSON 格式无效")

    for key in ("srt", "subtitle", "subtitles"):
        value = data.get(key)
        if isinstance(value, str) and "-->" in value:
            return value.rstrip() + "\n"

    segments = data.get("segments") or data.get("sentences") or data.get("result")
    if isinstance(segments, list):
        return _segments_to_srt([item for item in segments if isinstance(item, dict)])

    text_value = data.get("text") or data.get("transcript")
    if isinstance(text_value, str):
        return _plain_text_to_srt(text_value)

    raise ThirdPartyAsrError("ASR 返回结果中未找到 srt、segments 或 text 字段")


def write_srt_file(srt_content: str, subtitle_file: str = "") -> str:
    """写入 SRT 字幕文件并返回路径。"""
    if not subtitle_file:
        subtitle_file = os.path.join(utils.subtitle_dir(), f"third_party_asr_{int(time.time())}.srt")
    parent = os.path.dirname(subtitle_file)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(subtitle_file, "w", encoding="utf-8") as f:
        f.write(srt_content)
    return subtitle_file


def create_with_third_party_asr(
    local_file: str,
    subtitle_file: str = "",
    api_url: str = "",
    api_key: str = "",
    model: str = "",
    response_format: str = "srt",
    timeout: float = 600.0,
    session=requests,
) -> str:
    """调用自定义 ASR 接口转写本地音视频并生成 SRT 文件。"""
    api_url = _require_api_url(api_url)
    if not os.path.isfile(local_file):
        raise ThirdPartyAsrError(f"待转写文件不存在: {local_file}")

    try:
        data = {"response_format": _normalize_response_format(response_format)}
        if model:
            data["model"] = model
        with open(local_file, "rb") as file_obj:
            files = {"file": (os.path.basename(local_file), file_obj)}
            response = session.post(
                api_url,
                headers=_auth_headers(api_key),
                data=data,
                files=files,
                timeout=timeout,
            )
        srt_content = parse_asr_response_to_srt(response)
        output_file = write_srt_file(srt_content, subtitle_file)
        logger.info(f"ASR 字幕文件已生成: {output_file}")
        return output_file
    except ThirdPartyAsrError:
        raise
    except Exception as exc:
        raise ThirdPartyAsrError("ASR 字幕转写失败，请检查接口地址、鉴权、文件或网络状态") from exc
