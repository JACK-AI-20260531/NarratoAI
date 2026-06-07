import os
import glob
import json
import re
import time
import traceback
import streamlit as st
from loguru import logger

from app.config import config
from app.models.schema import VideoClipParams
from app.services.subtitle_text import decode_subtitle_bytes
from app.utils import utils, check_script
from webui.tools.generate_script_docu import generate_script_docu
from webui.tools.generate_script_short import generate_script_short
from webui.tools.generate_short_summary import generate_script_short_sunmmary
from webui.tools.movie_commentary import (
    build_movie_montage,
    is_movie_montage_running,
    is_movie_scene_matching_running,
    match_movie_scenes,
    render_movie_montage_status,
    render_movie_scene_match_status,
)


def render_script_panel(tr):
    """渲染脚本配置面板"""
    with st.container(border=True):
        st.write(tr("Video Script Configuration"))
        params = VideoClipParams()

        # 渲染脚本文件选择
        render_script_file(tr, params)

        # 渲染视频文件选择
        render_video_file(tr, params)

        # 获取当前选择的脚本类型
        script_path = st.session_state.get('video_clip_json_path', '')

        # 根据脚本类型显示不同的布局
        if script_path == "auto":
            # 画面解说
            render_video_details(tr)
        elif script_path == "short":
            # 短剧混剪
            render_short_generate_options(tr)
        elif script_path == "summary":
            # 短剧解说
            short_drama_summary(tr)
        elif script_path == "movie_commentary":
            # 电影原片解说
            render_movie_commentary_options(tr)
        elif script_path == "movie_montage":
            # 电影混剪
            render_movie_montage_options(tr)
        else:
            # 默认为空
            pass

        # 渲染脚本操作按钮
        render_script_buttons(tr, params)


def render_script_file(tr, params):
    """渲染脚本文件选择"""
    # 定义功能模式
    MODE_FILE = "file_selection"
    MODE_AUTO = "auto"
    MODE_SHORT = "short"
    MODE_SUMMARY = "summary"
    MODE_MOVIE_COMMENTARY = "movie_commentary"
    MODE_MOVIE_MONTAGE = "movie_montage"

    # 处理保存脚本后的模式切换（必须在 widget 实例化之前）
    if st.session_state.get('_switch_to_file_mode'):
        st.session_state['script_mode_selection'] = tr("Select/Upload Script")
        del st.session_state['_switch_to_file_mode']

    # 模式选项映射
    mode_options = {
        tr("Select/Upload Script"): MODE_FILE,
        tr("Auto Generate"): MODE_AUTO,
        tr("Short Generate"): MODE_SHORT,
        tr("Short Drama Summary"): MODE_SUMMARY,
        "电影原片解说": MODE_MOVIE_COMMENTARY,
        "电影混剪": MODE_MOVIE_MONTAGE,
    }
    
    # 获取当前状态
    current_path = st.session_state.get('video_clip_json_path', '')
    
    # 确定当前选中的模式索引
    default_index = 0
    mode_keys = list(mode_options.keys())
    
    if current_path == "auto":
        default_index = mode_keys.index(tr("Auto Generate"))
    elif current_path == "short":
        default_index = mode_keys.index(tr("Short Generate"))
    elif current_path == "summary":
        default_index = mode_keys.index(tr("Short Drama Summary"))
    elif current_path == "movie_commentary":
        default_index = mode_keys.index("电影原片解说")
    elif current_path == "movie_montage":
        default_index = mode_keys.index("电影混剪")
    else:
        default_index = mode_keys.index(tr("Select/Upload Script"))

    # 1. 渲染功能选择下拉框
    # 使用 segmented_control 替代 selectbox，提供更好的视觉体验
    default_mode_label = mode_keys[default_index]
    
    # 定义回调函数来处理状态更新
    def update_script_mode():
        # 获取当前选中的标签
        selected_label = st.session_state.script_mode_selection
        if selected_label:
            # 更新实际的 path 状态；文件选择模式不是脚本路径，不写入占位值
            new_mode = mode_options[selected_label]
            if new_mode == MODE_FILE:
                st.session_state.video_clip_json_path = ""
                params.video_clip_json_path = ""
            else:
                st.session_state.video_clip_json_path = new_mode
                params.video_clip_json_path = new_mode
        else:
            # 如果用户取消选择（segmented_control 允许取消），恢复到默认或上一个状态
            # 这里我们强制保持当前状态，或者重置为默认
            st.session_state.script_mode_selection = default_mode_label

    # 渲染组件
    selected_mode_label = st.segmented_control(
        tr("Video Type"),
        options=mode_keys,
        default=default_mode_label,
        key="script_mode_selection",
        on_change=update_script_mode
    )
    
    # 处理未选择的情况（虽然有default，但在某些交互下可能为空）
    if not selected_mode_label:
        selected_mode_label = default_mode_label
        
    selected_mode = mode_options[selected_mode_label]

    # 2. 根据选择的模式处理逻辑
    if selected_mode == MODE_FILE:
        # --- 文件选择模式 ---
        script_list = [
            (tr("None"), ""),
            (tr("Upload Script"), "upload_script")
        ]

        # 获取已有脚本文件
        script_dir = utils.script_dir()
        file_list = _get_saved_script_files()

        for file in file_list:
            display_name = file['file'].replace(config.root_dir, "")
            script_list.append((display_name, file['file']))

        # 找到保存的脚本文件在列表中的索引
        # 如果当前 path 是功能模式或占位值，则重置为空
        mode_values = {MODE_FILE, MODE_AUTO, MODE_SHORT, MODE_SUMMARY, MODE_MOVIE_COMMENTARY, MODE_MOVIE_MONTAGE, "upload_script"}
        saved_script_path = current_path if current_path not in mode_values else ""
        
        selected_index = 0
        for i, (_, path) in enumerate(script_list):
            if path == saved_script_path:
                selected_index = i
                break

        # 如果找到了保存的脚本，同步更新 selectbox 的 key 状态
        if saved_script_path and selected_index > 0:
            st.session_state['script_file_selection'] = selected_index

        selected_script_index = st.selectbox(
            tr("Script Files"),
            index=selected_index,
            options=range(len(script_list)),
            format_func=lambda x: script_list[x][0],
            key="script_file_selection"
        )

        script_path = script_list[selected_script_index][1]
        # 只有当用户实际选择了脚本时才更新路径，避免覆盖已保存的路径
        if script_path:
            st.session_state['video_clip_json_path'] = script_path
            params.video_clip_json_path = script_path
        elif saved_script_path:
            # 如果用户选择了 "None" 但之前有保存的脚本，保持原有路径
            st.session_state['video_clip_json_path'] = saved_script_path
            params.video_clip_json_path = saved_script_path

        # 处理脚本上传
        if script_path == "upload_script":
            uploaded_file = st.file_uploader(
                tr("Upload Script File"),
                type=["json"],
                accept_multiple_files=False,
            )

            if uploaded_file is not None:
                try:
                    # 读取上传的JSON内容并验证格式
                    script_content = uploaded_file.read().decode('utf-8')
                    json_data = json.loads(script_content)

                    # 保存到脚本目录
                    safe_filename = os.path.basename(uploaded_file.name)
                    script_file_path = os.path.join(script_dir, safe_filename)
                    file_name, file_extension = os.path.splitext(safe_filename)

                    # 如果文件已存在,添加时间戳
                    if os.path.exists(script_file_path):
                        timestamp = time.strftime("%Y%m%d%H%M%S")
                        file_name_with_timestamp = f"{file_name}_{timestamp}"
                        script_file_path = os.path.join(script_dir, file_name_with_timestamp + file_extension)

                    # 写入文件
                    with open(script_file_path, "w", encoding='utf-8') as f:
                        json.dump(json_data, f, ensure_ascii=False, indent=2)

                    # 更新状态
                    st.success(tr("Script Uploaded Successfully"))
                    st.session_state['video_clip_json_path'] = script_file_path
                    params.video_clip_json_path = script_file_path
                    time.sleep(1)
                    st.rerun()

                except json.JSONDecodeError:
                    st.error(tr("Invalid JSON format"))
                except Exception as e:
                    st.error(f"{tr('Upload failed')}: {str(e)}")
    else:
        # --- 功能生成模式 ---
        st.session_state['video_clip_json_path'] = selected_mode
        params.video_clip_json_path = selected_mode


def render_video_file(tr, params):
    """渲染视频文件选择"""
    video_list = [(tr("None"), ""), (tr("Upload Local Files"), "upload_local")]

    # 获取已有视频文件
    for suffix in ["*.mp4", "*.mov", "*.avi", "*.mkv"]:
        video_files = glob.glob(os.path.join(utils.video_dir(), suffix))
        for file in video_files:
            display_name = file.replace(config.root_dir, "")
            video_list.append((display_name, file))

    selected_video_index = st.selectbox(
        tr("Video File"),
        index=0,
        options=range(len(video_list)),
        format_func=lambda x: video_list[x][0]
    )

    video_path = video_list[selected_video_index][1]
    st.session_state['video_origin_path'] = video_path
    params.video_origin_path = video_path

    if video_path == "upload_local":
        uploaded_file = st.file_uploader(
            tr("Upload Local Files"),
            type=["mp4", "mov", "avi", "flv", "mkv"],
            accept_multiple_files=False,
        )

        if uploaded_file is not None:
            safe_filename = os.path.basename(uploaded_file.name)
            video_file_path = os.path.join(utils.video_dir(), safe_filename)
            file_name, file_extension = os.path.splitext(safe_filename)

            if os.path.exists(video_file_path):
                timestamp = time.strftime("%Y%m%d%H%M%S")
                file_name_with_timestamp = f"{file_name}_{timestamp}"
                video_file_path = os.path.join(utils.video_dir(), file_name_with_timestamp + file_extension)

            with open(video_file_path, "wb") as f:
                f.write(uploaded_file.read())
                st.success(tr("File Uploaded Successfully"))
                st.session_state['video_origin_path'] = video_file_path
                params.video_origin_path = video_file_path
                time.sleep(1)
                st.rerun()


def render_short_generate_options(tr):
    """
    渲染Short Generate模式下的特殊选项
    在Short Generate模式下，替换原有的输入框为自定义片段选项
    """
    short_drama_summary(tr)
    # 显示自定义片段数量选择器
    custom_clips = st.number_input(
        tr("自定义片段"),
        min_value=1,
        max_value=20,
        value=st.session_state.get('custom_clips', 5),
        help=tr("设置需要生成的短视频片段数量"),
        key="custom_clips_input"
    )
    st.session_state['custom_clips'] = custom_clips


def _get_saved_script_files():
    script_dir = utils.script_dir()
    files = glob.glob(os.path.join(script_dir, "*.json"))
    file_list = []
    for file in files:
        file_list.append({
            "name": os.path.basename(file),
            "file": file,
            "ctime": os.path.getctime(file)
        })
    file_list.sort(key=lambda x: x["ctime"], reverse=True)
    return file_list


def _safe_filename_part(value: str, default: str = "script") -> str:
    name = os.path.splitext(os.path.basename(value or ""))[0].strip()
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("._")
    return name or default


def _build_script_save_path():
    script_dir = utils.script_dir()
    timestamp = time.strftime("%Y-%m%d-%H%M%S")
    movie_name = _safe_filename_part(st.session_state.get('video_origin_path', ''), "script")
    save_path = os.path.join(script_dir, f"{movie_name}_{timestamp}.json")
    suffix = 1
    while os.path.exists(save_path):
        save_path = os.path.join(script_dir, f"{movie_name}_{timestamp}_{suffix:02d}.json")
        suffix += 1
    return save_path


def _load_saved_video_script(script_path: str):
    with open(script_path, 'r', encoding='utf-8') as f:
        script = utils.clean_model_output(f.read())
    data = json.loads(script)
    st.session_state['video_clip_json'] = data
    st.session_state['video_clip_json_path'] = script_path
    return data


def _render_movie_commentary_script_picker():
    file_list = _get_saved_script_files()
    script_options = [("不选择历史脚本", "")]
    for file in file_list:
        display_name = file['file'].replace(config.root_dir, "")
        script_options.append((display_name, file['file']))

    selected_index = st.selectbox(
        "选择已生成的脚本文件",
        index=0,
        options=range(len(script_options)),
        format_func=lambda x: script_options[x][0],
        key="movie_commentary_saved_script_selection",
    )
    selected_path = script_options[selected_index][1]
    if selected_path and st.button("加载已生成脚本", key="movie_commentary_load_saved_script", use_container_width=True):
        try:
            _load_saved_video_script(selected_path)
            st.success("脚本加载成功")
            st.rerun()
        except Exception as e:
            logger.error(f"加载电影原片解说历史脚本失败\n{traceback.format_exc()}")
            st.error(f"脚本加载失败: {str(e)}")


def render_movie_commentary_options(tr, key_prefix="movie_commentary"):
    """电影原片解说模式：根据解说脚本匹配原片画面。"""
    st.markdown("**电影原片解说**")
    render_movie_scene_match_status()
    _render_movie_commentary_script_picker()

    commentary_script = st.text_area(
        "电影原片解说脚本",
        value=st.session_state.get('movie_commentary_script', ''),
        height=180,
        placeholder="粘贴或输入你的电影解说文案，点击下方按钮后会按文案匹配原始电影画面。",
        key=f"{key_prefix}_script_input",
    )
    st.session_state['movie_commentary_script'] = commentary_script

    option_cols = st.columns(3)
    with option_cols[0]:
        st.number_input(
            tr("Frame Interval (seconds)"),
            min_value=1,
            value=st.session_state.get('frame_interval_input', config.frames.get('frame_interval_input', 3)),
            help="抽取原片关键帧的间隔，数值越小匹配越细，但消耗更多视觉模型 token。",
            key=f"{key_prefix}_frame_interval_input",
        )
        st.session_state['frame_interval_input'] = st.session_state.get(f"{key_prefix}_frame_interval_input")
    with option_cols[1]:
        st.number_input(
            tr("Batch Size"),
            min_value=1,
            value=st.session_state.get('vision_batch_size', config.frames.get('vision_batch_size', 10)),
            help="每批送入视觉模型分析的关键帧数量。",
            key=f"{key_prefix}_vision_batch_size",
        )
        st.session_state['vision_batch_size'] = st.session_state.get(f"{key_prefix}_vision_batch_size")
    with option_cols[2]:
        st.number_input(
            "候选画面数",
            min_value=1,
            max_value=10,
            value=st.session_state.get('movie_match_top_k', 3),
            help="每段解说保留的候选原片画面数量。",
            key=f"{key_prefix}_match_top_k",
        )
        st.session_state['movie_match_top_k'] = st.session_state.get(f"{key_prefix}_match_top_k")


def render_movie_montage_options(tr, key_prefix="movie_montage"):
    """电影混剪模式：按主题从原片挑选镜头。"""
    st.markdown("**电影混剪**")
    st.caption("输入混剪主题或关键词，系统会从原始电影中自动挑选相关高能片段，生成可编辑的视频脚本。")
    render_movie_montage_status()

    theme_key = f"{key_prefix}_theme_input"
    clip_count_key = f"{key_prefix}_clip_count_input"
    clip_duration_key = f"{key_prefix}_clip_duration_input"
    if theme_key not in st.session_state:
        st.session_state[theme_key] = st.session_state.get('movie_montage_theme', '')
    if clip_count_key not in st.session_state:
        st.session_state[clip_count_key] = st.session_state.get('movie_montage_clip_count', 8)
    if clip_duration_key not in st.session_state:
        st.session_state[clip_duration_key] = st.session_state.get('movie_montage_clip_duration', 3.0)

    st.text_area(
        "混剪主题 / 关键词",
        height=100,
        placeholder="例如：男主高能反击、悬疑反转、爱情名场面、动作追逐、悲伤催泪片段",
        key=theme_key,
        on_change=lambda: st.session_state.update({'movie_montage_theme': st.session_state.get(theme_key, '')}),
    )
    st.session_state['movie_montage_theme'] = st.session_state.get(theme_key, '')

    option_cols = st.columns(4)
    with option_cols[0]:
        st.number_input(
            tr("Frame Interval (seconds)"),
            min_value=1,
            value=st.session_state.get('frame_interval_input', config.frames.get('frame_interval_input', 3)),
            help="抽取原片关键帧的间隔，数值越小匹配越细，但消耗更多视觉模型 token。",
            key=f"{key_prefix}_frame_interval_input",
        )
        st.session_state['frame_interval_input'] = st.session_state.get(f"{key_prefix}_frame_interval_input")
    with option_cols[1]:
        st.number_input(
            tr("Batch Size"),
            min_value=1,
            value=st.session_state.get('vision_batch_size', config.frames.get('vision_batch_size', 10)),
            help="每批送入视觉模型分析的关键帧数量。",
            key=f"{key_prefix}_vision_batch_size",
        )
        st.session_state['vision_batch_size'] = st.session_state.get(f"{key_prefix}_vision_batch_size")
    with option_cols[2]:
        st.number_input(
            "混剪片段数",
            min_value=1,
            max_value=50,
            help="最终混剪脚本中生成多少个原片片段。",
            key=clip_count_key,
            on_change=lambda: st.session_state.update({'movie_montage_clip_count': st.session_state.get(clip_count_key, 8)}),
        )
        st.session_state['movie_montage_clip_count'] = st.session_state.get(clip_count_key, 8)
    with option_cols[3]:
        st.number_input(
            "单段时长（秒）",
            min_value=1.0,
            max_value=30.0,
            step=0.5,
            help="每个混剪镜头默认截取的时长。",
            key=clip_duration_key,
            on_change=lambda: st.session_state.update({'movie_montage_clip_duration': st.session_state.get(clip_duration_key, 3.0)}),
        )
        st.session_state['movie_montage_clip_duration'] = st.session_state.get(clip_duration_key, 3.0)


# AI生成画面解说 渲染视频主题和提示词
def render_video_details(tr):
    """画面解说 渲染视频主题和提示词"""
    video_theme = st.text_input(tr("Video Theme"))
    custom_prompt = st.text_area(
        tr("Generation Prompt"),
        value=st.session_state.get('video_plot', ''),
        help=tr("Custom prompt for LLM, leave empty to use default prompt"),
        height=180
    )
    # 非短视频模式下显示原有的三个输入框
    input_cols = st.columns(2)

    with input_cols[0]:
        st.number_input(
            tr("Frame Interval (seconds)"),
            min_value=0,
            value=st.session_state.get('frame_interval_input', config.frames.get('frame_interval_input', 3)),
            help=tr("Frame Interval (seconds) (More keyframes consume more tokens)"),
            key="frame_interval_input"
        )

    with input_cols[1]:
        st.number_input(
            tr("Batch Size"),
            min_value=0,
            value=st.session_state.get('vision_batch_size', config.frames.get('vision_batch_size', 10)),
            help=tr("Batch Size (More keyframes consume more tokens)"),
            key="vision_batch_size"
        )
    st.session_state['video_theme'] = video_theme
    st.session_state['custom_prompt'] = custom_prompt
    return video_theme, custom_prompt


def short_drama_summary(tr):
    """短剧解说 渲染视频主题和提示词"""
    # 检查是否已经处理过字幕文件
    if 'subtitle_file_processed' not in st.session_state:
        st.session_state['subtitle_file_processed'] = False

    render_fun_asr_transcription(tr)
    
    subtitle_file = st.file_uploader(
        tr("上传字幕文件"),
        type=["srt"],
        accept_multiple_files=False,
        key="subtitle_file_uploader"  # 添加唯一key
    )
    
    # 显示当前已上传的字幕文件路径
    if 'subtitle_path' in st.session_state and st.session_state['subtitle_path']:
        st.info(f"已上传字幕: {os.path.basename(st.session_state['subtitle_path'])}")
        if st.button(tr("清除已上传字幕")):
            st.session_state['subtitle_path'] = None
            st.session_state['subtitle_content'] = None
            st.session_state['subtitle_file_processed'] = False
            st.rerun()
    
    # 只有当有文件上传且尚未处理时才执行处理逻辑
    if subtitle_file is not None and not st.session_state['subtitle_file_processed']:
        try:
            # 清理文件名，防止路径污染和路径遍历攻击
            safe_filename = os.path.basename(subtitle_file.name)

            decoded = decode_subtitle_bytes(subtitle_file.getvalue())
            script_content = decoded.text
            detected_encoding = decoded.encoding

            if not script_content:
                st.error(tr("无法读取字幕文件，请检查文件编码（支持 UTF-8、UTF-16、GBK、GB2312）"))
                st.stop()

            # 验证字幕内容（简单检查）
            if len(script_content.strip()) < 10:
                st.warning(tr("字幕文件内容似乎为空，请检查文件"))

            # 保存到字幕目录
            script_file_path = os.path.join(utils.subtitle_dir(), safe_filename)
            file_name, file_extension = os.path.splitext(safe_filename)

            # 如果文件已存在,添加时间戳
            if os.path.exists(script_file_path):
                timestamp = time.strftime("%Y%m%d%H%M%S")
                file_name_with_timestamp = f"{file_name}_{timestamp}"
                script_file_path = os.path.join(utils.subtitle_dir(), file_name_with_timestamp + file_extension)

            # 直接写入SRT内容（统一使用 UTF-8）
            with open(script_file_path, "w", encoding='utf-8') as f:
                f.write(script_content)

            # 更新状态
            st.success(
                f"{tr('字幕上传成功')} "
                f"(编码: {detected_encoding.upper()}, "
                f"大小: {len(script_content)} 字符)"
            )
            st.session_state['subtitle_path'] = script_file_path
            st.session_state['subtitle_content'] = script_content
            st.session_state['subtitle_file_processed'] = True  # 标记已处理

            # 避免使用rerun，使用更新状态的方式
            # st.rerun()

        except Exception as e:
            st.error(f"{tr('Upload failed')}: {str(e)}")

    # 名称输入框
    video_theme = st.text_input(tr("短剧名称"))
    st.session_state['video_theme'] = video_theme
    # 数字输入框
    temperature = st.slider("temperature", 0.0, 2.0, 0.7)
    st.session_state['temperature'] = temperature
    return video_theme


def render_fun_asr_transcription(tr):
    """使用阿里百炼 Fun-ASR 从本地音视频转写生成字幕。"""
    def clear_fun_asr_subtitle_state():
        st.session_state['subtitle_path'] = None
        st.session_state['subtitle_content'] = None
        st.session_state['subtitle_file_processed'] = False

    with st.expander("阿里百炼 Fun-ASR 字幕转录", expanded=False):
        st.caption("上传本地音频/视频后，将自动上传到阿里百炼临时存储并通过 fun-asr 生成 SRT 字幕。")
        st.markdown(
            "API Key 获取地址："
            "[https://bailian.console.aliyun.com/?tab=model#/api-key]"
            "(https://bailian.console.aliyun.com/?tab=model#/api-key)"
        )

        api_key = st.text_input(
            "阿里百炼 API Key",
            value=config.fun_asr.get("api_key", ""),
            type="password",
            help="请输入你自己的阿里百炼 API Key；保存配置后会写入本地 config.toml",
            key="fun_asr_api_key",
        )
        uploaded_media = st.file_uploader(
            "上传需要转录的音频/视频",
            type=[
                "aac", "amr", "avi", "flac", "flv", "m4a", "mkv", "mov",
                "mp3", "mp4", "mpeg", "ogg", "opus", "wav", "webm", "wma", "wmv",
            ],
            accept_multiple_files=False,
            key="fun_asr_media_uploader",
        )

        if st.button("转写生成字幕", key="fun_asr_transcribe"):
            if not api_key.strip():
                clear_fun_asr_subtitle_state()
                st.error("请先输入阿里百炼 API Key")
                return
            if uploaded_media is None:
                clear_fun_asr_subtitle_state()
                st.error("请先上传需要转录的音频或视频文件")
                return

            try:
                clear_fun_asr_subtitle_state()
                from app.services import fun_asr_subtitle

                config.fun_asr["api_key"] = api_key.strip()
                config.fun_asr["model"] = "fun-asr"
                config.save_config()

                temp_dir = utils.temp_dir("fun_asr")
                safe_filename = os.path.basename(uploaded_media.name)
                media_path = os.path.join(temp_dir, safe_filename)
                file_name, file_extension = os.path.splitext(safe_filename)
                if os.path.exists(media_path):
                    timestamp = time.strftime("%Y%m%d%H%M%S")
                    media_path = os.path.join(temp_dir, f"{file_name}_{timestamp}{file_extension}")

                with open(media_path, "wb") as f:
                    f.write(uploaded_media.getbuffer())

                subtitle_name = f"{os.path.splitext(os.path.basename(media_path))[0]}_fun_asr.srt"
                subtitle_path = os.path.join(utils.subtitle_dir(), subtitle_name)

                with st.spinner("正在使用阿里百炼 Fun-ASR 转写字幕，请稍候..."):
                    generated_path = fun_asr_subtitle.create_with_fun_asr(
                        local_file=media_path,
                        subtitle_file=subtitle_path,
                        api_key=api_key.strip(),
                    )

                if not generated_path or not os.path.exists(generated_path):
                    clear_fun_asr_subtitle_state()
                    st.error("Fun-ASR 转写失败：未生成字幕文件")
                    return

                with open(generated_path, "r", encoding="utf-8") as f:
                    subtitle_content = f.read()

                st.session_state['subtitle_path'] = generated_path
                st.session_state['subtitle_content'] = subtitle_content
                st.session_state['subtitle_file_processed'] = True
                st.success(f"字幕转写成功: {os.path.basename(generated_path)}")
            except Exception as e:
                clear_fun_asr_subtitle_state()
                logger.error(f"Fun-ASR 字幕转写失败: {traceback.format_exc()}")
                st.error(f"Fun-ASR 字幕转写失败: {str(e)}")


def render_script_buttons(tr, params):
    """渲染脚本操作按钮"""
    # 获取当前选择的脚本类型
    script_path = st.session_state.get('video_clip_json_path', '')

    # 生成/加载按钮
    if script_path == "auto":
        button_name = tr("Generate Video Script")
    elif script_path == "short":
        button_name = tr("Generate Short Video Script")
    elif script_path == "summary":
        button_name = tr("生成短剧解说脚本")
    elif script_path == "movie_commentary":
        button_name = "匹配原片画面"
    elif script_path == "movie_montage":
        button_name = "生成电影混剪"
    elif script_path.endswith("json"):
        button_name = tr("Load Video Script")
    else:
        button_name = tr("Please Select Script File")

    action_disabled = (
        not script_path
        or (script_path == "movie_commentary" and is_movie_scene_matching_running())
        or (script_path == "movie_montage" and is_movie_montage_running())
    )
    if st.button(button_name, key="script_action", disabled=action_disabled):
        if script_path == "auto":
            # 执行纪录片视频脚本生成（视频无字幕无配音）
            generate_script_docu(params)
        elif script_path == "short":
            # 执行 短剧混剪 脚本生成
            custom_clips = st.session_state.get('custom_clips')
            generate_script_short(tr, params, custom_clips)
        elif script_path == "summary":
            # 执行 短剧解说 脚本生成
            subtitle_path = st.session_state.get('subtitle_path')
            video_theme = st.session_state.get('video_theme')
            temperature = st.session_state.get('temperature')
            generate_script_short_sunmmary(params, subtitle_path, video_theme, temperature)
        elif script_path == "movie_commentary":
            # 执行电影原片解说画面匹配
            match_movie_scenes(params)
        elif script_path == "movie_montage":
            # 执行电影混剪
            build_movie_montage(params)
        else:
            load_script(tr, script_path)

    # 视频脚本编辑区
    video_clip_json_details = st.text_area(
        tr("Video Script"),
        value=json.dumps(st.session_state.get('video_clip_json', []), indent=2, ensure_ascii=False),
        height=500
    )

    # 操作按钮行 - 合并格式检查和保存功能
    if st.button(tr("Save Script"), key="save_script", use_container_width=True):
        save_script_with_validation(tr, video_clip_json_details)


def load_script(tr, script_path):
    """加载脚本文件"""
    if not script_path or script_path in {"file_selection", "upload_script", "auto", "short", "summary", "movie_commentary", "movie_montage"}:
        st.warning(tr("Please Select Script File"))
        return
    if not os.path.isfile(script_path):
        st.error(f"{tr('Failed to load script')}: {script_path}")
        return

    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            script = f.read()
            script = utils.clean_model_output(script)
            st.session_state['video_clip_json'] = json.loads(script)
            st.success(tr("Script loaded successfully"))
            st.rerun()
    except Exception as e:
        logger.error(f"加载脚本文件时发生错误\n{traceback.format_exc()}")
        st.error(f"{tr('Failed to load script')}: {str(e)}")


def save_script_with_validation(tr, video_clip_json_details):
    """保存视频脚本（包含格式验证）"""
    if not video_clip_json_details:
        st.error(tr("请输入视频脚本"))
        st.stop()

    # 第一步：格式验证
    with st.spinner("正在验证脚本格式..."):
        try:
            result = check_script.check_format(video_clip_json_details)
            if not result.get('success'):
                # 格式验证失败，显示详细错误信息
                error_message = result.get('message', '未知错误')
                error_details = result.get('details', '')

                st.error(f"**脚本格式验证失败**")
                st.error(f"**错误信息：** {error_message}")
                if error_details:
                    st.error(f"**详细说明：** {error_details}")

                # 显示正确格式示例
                st.info("**正确的脚本格式示例：**")
                example_script = [
                    {
                        "_id": 1,
                        "timestamp": "00:00:00,600-00:00:07,559",
                        "picture": "工地上，蔡晓艳奋力救人，场面混乱",
                        "narration": "灾后重建，工地上险象环生！泼辣女工蔡晓艳挺身而出，救人第一！",
                        "OST": 0
                    },
                    {
                        "_id": 2,
                        "timestamp": "00:00:08,240-00:00:12,359",
                        "picture": "领导视察，蔡晓艳不屑一顾",
                        "narration": "播放原片4",
                        "OST": 1
                    }
                ]
                st.code(json.dumps(example_script, ensure_ascii=False, indent=2), language='json')
                st.stop()

        except Exception as e:
            st.error(f"格式验证过程中发生错误: {str(e)}")
            st.stop()

    # 第二步：保存脚本
    with st.spinner(tr("Save Script")):
        save_path = _build_script_save_path()

        try:
            data = json.loads(video_clip_json_details)
            with open(save_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, ensure_ascii=False, indent=4)
                st.session_state['video_clip_json'] = data
                st.session_state['video_clip_json_path'] = save_path
                
                # 标记需要切换到文件选择模式（在下次渲染前处理）
                st.session_state['_switch_to_file_mode'] = True

                # 更新配置
                config.app["video_clip_json_path"] = save_path

                # 显示成功消息
                st.success("✅ 脚本格式验证通过，保存成功！")

                # 强制重新加载页面更新选择框
                time.sleep(0.5)  # 给一点时间让用户看到成功消息
                st.rerun()

        except Exception as err:
            st.error(f"{tr('Failed to save script')}: {str(err)}")
            st.stop()


# crop_video函数已移除 - 现在使用统一裁剪策略，不再需要预裁剪步骤


def get_script_params():
    """获取脚本参数"""
    return {
        'video_language': st.session_state.get('video_language', ''),
        'video_clip_json_path': st.session_state.get('video_clip_json_path', ''),
        'video_origin_path': st.session_state.get('video_origin_path', ''),
        'video_name': st.session_state.get('video_name', ''),
        'video_plot': st.session_state.get('video_plot', '')
    }
