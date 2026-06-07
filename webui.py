import streamlit as st
import os
import sys
import time
from loguru import logger
from app.config import config
from webui.components import basic_settings, video_settings, audio_settings, subtitle_settings, script_settings, \
    system_settings
# from webui.utils import cache, file_utils
from app.utils import utils
from app.utils import ffmpeg_utils
from app.models.schema import VideoClipParams, VideoAspect


# 初始化配置 - 必须是第一个 Streamlit 命令
st.set_page_config(
    page_title="JackAI",
    page_icon="📽️",
    layout="wide",
    initial_sidebar_state="auto",
    menu_items={
        "Report a bug": "https://github.com/linyqh/NarratoAI/issues",
        'About': f"# Jack:blue[AI] :sunglasses: 📽️ \n #### Version: v{config.project_version} \n "
                 f"自动化影视解说视频工具"
    },
)

# 设置页面样式
hide_streamlit_style = """
<style>#root > div:nth-child(1) > div > div > div > div > section > div {padding-top: 2rem; padding-bottom: 10px; padding-left: 20px; padding-right: 20px;}</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)


def init_log():
    """初始化日志配置"""
    from loguru import logger
    logger.remove()
    _lvl = "INFO"  # 改为 INFO 级别，过滤掉 DEBUG 日志

    def format_record(record):
        # 简化日志格式化处理，不尝试按特定字符串过滤torch相关内容
        file_path = record["file"].path
        relative_path = os.path.relpath(file_path, config.root_dir)
        record["file"].path = f"./{relative_path}"
        record['message'] = record['message'].replace(config.root_dir, ".")

        _format = '<green>{time:%Y-%m-%d %H:%M:%S}</> | ' + \
                  '<level>{level}</> | ' + \
                  '"{file.path}:{line}":<blue> {function}</> ' + \
                  '- <level>{message}</>' + "\n"
        return _format

    # 添加日志过滤器
    def log_filter(record):
        """过滤不必要的日志消息"""
        # 过滤掉启动时的噪音日志（即使在 DEBUG 模式下也可以选择过滤）
        ignore_patterns = [
            "Examining the path of torch.classes raised",
            "torch.cuda.is_available()",
            "CUDA initialization"
        ]
        return not any(pattern in record["message"] for pattern in ignore_patterns)

    logger.add(
        sys.stdout,
        level=_lvl,
        format=format_record,
        colorize=True,
        filter=log_filter
    )

    # 应用启动后，可以再添加更复杂的过滤器
    def setup_advanced_filters():
        """在应用完全启动后设置高级过滤器"""
        try:
            for handler_id in logger._core.handlers:
                logger.remove(handler_id)

            # 重新添加带有高级过滤的处理器
            def advanced_filter(record):
                """更复杂的过滤器，在应用启动后安全使用"""
                ignore_messages = [
                    "Examining the path of torch.classes raised",
                    "torch.cuda.is_available()",
                    "CUDA initialization"
                ]
                return not any(msg in record["message"] for msg in ignore_messages)

            logger.add(
                sys.stdout,
                level=_lvl,
                format=format_record,
                colorize=True,
                filter=advanced_filter
            )
        except Exception as e:
            # 如果过滤器设置失败，确保日志仍然可用
            logger.add(
                sys.stdout,
                level=_lvl,
                format=format_record,
                colorize=True
            )
            logger.error(f"设置高级日志过滤器失败: {e}")

    # 将高级过滤器设置放到启动主逻辑后
    import threading
    threading.Timer(5.0, setup_advanced_filters).start()


def init_global_state():
    """初始化全局状态"""
    if 'video_clip_json' not in st.session_state:
        st.session_state['video_clip_json'] = []
    if 'video_plot' not in st.session_state:
        st.session_state['video_plot'] = ''
    if 'ui_language' not in st.session_state:
        st.session_state['ui_language'] = config.ui.get("language", utils.get_system_locale())
    # 移除subclip_videos初始化 - 现在使用统一裁剪策略


def tr(key):
    """翻译函数"""
    i18n_dir = os.path.join(os.path.dirname(__file__), "webui", "i18n")
    locales = utils.load_locales(i18n_dir)
    loc = locales.get(st.session_state['ui_language'], {})
    return loc.get("Translation", {}).get(key, key)


def copy_file_to_clipboard(file_path: str):
    """把文件写入 Windows 剪贴板，支持在资源管理器中粘贴。"""
    import ctypes
    import os
    from ctypes import wintypes

    absolute_path = os.path.abspath(file_path)
    encoded_path = absolute_path + "\0\0"
    dropfiles_size = 20
    data = encoded_path.encode("utf-16le")
    total_size = dropfiles_size + len(data)

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32

    class DROPFILES(ctypes.Structure):
        _fields_ = [
            ("pFiles", wintypes.DWORD),
            ("pt_x", wintypes.LONG),
            ("pt_y", wintypes.LONG),
            ("fNC", wintypes.BOOL),
            ("fWide", wintypes.BOOL),
        ]

    GMEM_MOVEABLE = 0x0002
    CF_HDROP = 15
    DROPEFFECT_COPY = 1

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    drop_effect_format = user32.RegisterClipboardFormatW("Preferred DropEffect")
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT

    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, total_size)
    if not handle:
        raise OSError("GlobalAlloc failed")

    effect_handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(wintypes.DWORD))
    if not effect_handle:
        kernel32.GlobalFree(handle)
        raise OSError("GlobalAlloc drop effect failed")

    clipboard_opened = False
    try:
        locked = kernel32.GlobalLock(handle)
        if not locked:
            raise OSError("GlobalLock failed")

        dropfiles = DROPFILES()
        dropfiles.pFiles = dropfiles_size
        dropfiles.pt_x = 0
        dropfiles.pt_y = 0
        dropfiles.fNC = False
        dropfiles.fWide = True

        ctypes.memmove(locked, ctypes.byref(dropfiles), dropfiles_size)
        ctypes.memmove(locked + dropfiles_size, data, len(data))
        kernel32.GlobalUnlock(handle)

        effect_locked = kernel32.GlobalLock(effect_handle)
        if not effect_locked:
            raise OSError("GlobalLock drop effect failed")
        effect_value = wintypes.DWORD(DROPEFFECT_COPY)
        ctypes.memmove(effect_locked, ctypes.byref(effect_value), ctypes.sizeof(effect_value))
        kernel32.GlobalUnlock(effect_handle)

        if not user32.OpenClipboard(None):
            raise OSError("OpenClipboard failed")
        clipboard_opened = True
        if not user32.EmptyClipboard():
            raise OSError("EmptyClipboard failed")
        if not user32.SetClipboardData(CF_HDROP, handle):
            raise OSError("SetClipboardData failed")
        handle = None
        if drop_effect_format and not user32.SetClipboardData(drop_effect_format, effect_handle):
            raise OSError("SetClipboardData drop effect failed")
        effect_handle = None
    finally:
        if clipboard_opened:
            user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)
        if effect_handle:
            kernel32.GlobalFree(effect_handle)


def render_copy_video_button(source_video: str):
    """渲染复制视频按钮，点击时只刷新按钮区域。"""
    import os

    if st.button("复制视频", use_container_width=True, key="copy_generated_video"):
        if not os.path.exists(source_video):
            st.error(f"视频文件不存在: {source_video}")
            return

        try:
            copy_file_to_clipboard(source_video)
            st.success("视频文件已复制到剪贴板，可粘贴到任意文件夹")
        except Exception as e:
            logger.error(f"复制视频到剪贴板失败: {e}")
            st.error(f"复制失败: {e}")


if hasattr(st, "fragment"):
    render_copy_video_button = st.fragment(render_copy_video_button)


def render_generate_button():
    """渲染生成按钮和处理逻辑"""
    from app.services import task as tm
    from app.services import state as sm
    from app.models import const
    import threading
    import uuid

    task_id = st.session_state.get('generate_video_task_id', '')
    task = sm.state.get_task(task_id) if task_id else None
    is_running = bool(task and task.get("state") == const.TASK_STATE_PROCESSING)

    if task:
        progress = int(task.get("progress", 0) or 0)
        state = task.get("state")
        st.progress(progress / 100)

        if state == const.TASK_STATE_PROCESSING:
            st.info(f"Processing... {progress}%")
        elif state == const.TASK_STATE_COMPLETE:
            st.success(tr("视频生成完成"))
            video_files = task.get("videos", [])
            try:
                if video_files:
                    player_cols = st.columns(len(video_files) * 2 + 1)
                    for i, url in enumerate(video_files):
                        player_cols[i * 2 + 1].video(url)

                    render_copy_video_button(video_files[0])
            except Exception as e:
                logger.error(f"播放视频失败: {e}")
        elif state == const.TASK_STATE_FAILED:
            st.error(f"任务失败: {task.get('message', 'Unknown error')}")

    if st.button(tr("Generate Video"), use_container_width=True, type="primary", disabled=is_running):
        config.save_config()

        if not st.session_state.get('video_clip_json_path'):
            st.error(tr("脚本文件不能为空"))
            return
        if not st.session_state.get('video_origin_path'):
            st.error(tr("视频文件不能为空"))
            return

        script_params = script_settings.get_script_params()
        video_params = video_settings.get_video_params()
        audio_params = audio_settings.get_audio_params()
        subtitle_params = subtitle_settings.get_subtitle_params()

        all_params = {
            **script_params,
            **video_params,
            **audio_params,
            **subtitle_params
        }
        params = VideoClipParams(**all_params)
        new_task_id = str(uuid.uuid4())
        st.session_state['generate_video_task_id'] = new_task_id
        sm.state.update_task(new_task_id, state=const.TASK_STATE_PROCESSING, progress=0)

        def run_task():
            try:
                tm.start_subclip_unified(
                    task_id=new_task_id,
                    params=params
                )
            except Exception as e:
                logger.error(f"任务执行失败: {e}")
                sm.state.update_task(new_task_id, state=const.TASK_STATE_FAILED, message=str(e))

        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()
        st.rerun()

    if is_running:
        time.sleep(1)
        st.rerun()


def get_voice_name_for_tts_engine(tts_engine: str) -> str:
    """根据TTS引擎获取用户选择的音色"""
    if tts_engine == 'doubaotts':
        return st.session_state.get('voice_name', config.ui.get('doubaotts_voice_type', 'BV700_streaming'))
    elif tts_engine == 'azure_speech':
        return st.session_state.get('voice_name', config.ui.get('azure_voice_name', 'zh-CN-XiaoxiaoMultilingualNeural'))
    else:
        return st.session_state.get('voice_name', config.ui.get('edge_voice_name', 'zh-CN-XiaoxiaoNeural-Female'))


def get_jianying_export_params() -> VideoClipParams:
    """获取导出到剪映草稿的参数"""
    tts_engine = st.session_state.get('tts_engine', 'azure')
    voice_name = get_voice_name_for_tts_engine(tts_engine)
    voice_rate = st.session_state.get('voice_rate', 1.0)
    voice_pitch = st.session_state.get('voice_pitch', 1.0)
    
    return VideoClipParams(
        video_clip_json_path=st.session_state['video_clip_json_path'],
        video_origin_path=st.session_state['video_origin_path'],
        tts_engine=tts_engine,
        voice_name=voice_name,
        voice_rate=voice_rate,
        voice_pitch=voice_pitch,
        n_threads=config.app.get('n_threads', 4),
        video_aspect=VideoAspect.landscape,
        subtitle_enabled=st.session_state.get('subtitle_enabled', False),
        font_name=st.session_state.get('font_name', 'Microsoft YaHei'),
        font_size=st.session_state.get('font_size', 24),
        text_fore_color=st.session_state.get('text_fore_color', '#FFFFFF'),
        subtitle_position=st.session_state.get('subtitle_position', 'bottom'),
        custom_position=st.session_state.get('custom_position', 70.0),
        tts_volume=st.session_state.get('tts_volume', 1.0),
        original_volume=st.session_state.get('original_volume', 0.7),
        bgm_volume=st.session_state.get('bgm_volume', 0.3),
        draft_name=st.session_state.get('draft_name_input', f"JackAI_{int(time.time())}")
    )


def render_export_jianying_button():
    """渲染导出到剪映草稿按钮和处理逻辑"""
    import os
    import time
    import uuid
    from app.services import state as sm
    from app.models import const
    from loguru import logger
    
    # 初始化session state
    if 'show_jianying_export_form' not in st.session_state:
        st.session_state['show_jianying_export_form'] = False
    if 'jianying_export_result' not in st.session_state:
        st.session_state['jianying_export_result'] = None
    if 'jianying_export_error' not in st.session_state:
        st.session_state['jianying_export_error'] = None

    generate_task_id = st.session_state.get('generate_video_task_id', '')
    generate_task = sm.state.get_task(generate_task_id) if generate_task_id else None
    video_generated = bool(generate_task and generate_task.get("state") == const.TASK_STATE_COMPLETE)

    st.markdown(
        """
        <style>
        .st-key-jianying_export_button button[kind="primary"]:not(:disabled) {
            background-color: #16a34a;
            border-color: #16a34a;
        }
        .st-key-jianying_export_button button[kind="primary"]:hover:not(:disabled) {
            background-color: #15803d;
            border-color: #15803d;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if st.session_state.get('jianying_export_result'):
        result = st.session_state['jianying_export_result']
        st.success(f"✅ 成功导出到剪映草稿: {result['draft_name']}")
        st.info(f"📁 草稿已保存到: {result['draft_path']}")
        st.info("请直接打开本地安装的剪映软件，即可操作导入到剪映的视频。")
    elif st.session_state.get('jianying_export_error'):
        st.error(f"❌ 导出到剪映草稿失败: {st.session_state['jianying_export_error']}")

    # 显示导出表单
    if st.session_state['show_jianying_export_form']:
        st.markdown("---")
        st.subheader("导出到剪映草稿")
        
        draft_name = st.text_input(
            "请输入剪映草稿名称",
            value=f"JackAI_{int(time.time())}",
            key="draft_name_input"
        )

        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            confirm_export = st.button("确认导出", key="confirm_export", use_container_width=True)
        with cancel_col:
            cancel_export = st.button("取消", key="cancel_export", use_container_width=True)
        
        if confirm_export:
            if not draft_name:
                st.error("请输入草稿名称")
                return
            
            # 创建任务ID
            task_id = str(uuid.uuid4())
            st.session_state['task_id'] = task_id
            
            # 构建参数
            try:
                params = get_jianying_export_params()
            except Exception as e:
                logger.error(f"构建参数失败: {e}")
                st.error(f"参数构建失败: {e}")
                return
            
            with st.spinner("正在导出到剪映草稿，请稍候..."):
                try:
                    from app.services import jianying_task
                    
                    # 调用导出到剪映草稿的任务
                    result = jianying_task.start_export_jianying_draft(task_id, params)
                    
                    # 记录日志
                    logger.info(f"成功导出到剪映草稿: {result['draft_name']}")
                    logger.info(f"草稿已保存到: {result['draft_path']}")
                    
                    # 保存结果到session state
                    st.session_state['jianying_export_result'] = result
                    st.session_state['jianying_export_error'] = None
                    st.session_state['show_jianying_export_form'] = False
                    st.rerun()
                except Exception as e:
                    logger.error(f"导出到剪映草稿失败: {e}")
                    import traceback
                    logger.error(f"错误详情: {traceback.format_exc()}")
                    st.session_state['jianying_export_error'] = str(e)
                    st.session_state['jianying_export_result'] = None
                    st.session_state['show_jianying_export_form'] = False
                    st.rerun()
        
        if cancel_export:
            st.session_state['show_jianying_export_form'] = False
            st.session_state['jianying_export_result'] = None
            st.session_state['jianying_export_error'] = None
            st.rerun()
    
    show_export_button = not st.session_state['show_jianying_export_form'] and not st.session_state.get('jianying_export_result')
    if show_export_button and st.button("📤 导出到剪映草稿", key="jianying_export_button", use_container_width=True, type="primary", disabled=not video_generated):
        config.save_config()
        
        if not st.session_state.get('video_clip_json_path'):
            st.error("脚本文件不能为空")
            return
        if not st.session_state.get('video_origin_path'):
            st.error("视频文件不能为空")
            return
        
        jianying_draft_path = config.ui.get("jianying_draft_path", "")
        if not jianying_draft_path:
            st.error("请在基础设置中配置剪映草稿地址")
            return
        
        if not os.path.exists(jianying_draft_path):
            st.error(f"剪映草稿文件夹不存在: {jianying_draft_path}")
            return
        
        # 显示导出表单
        st.session_state['show_jianying_export_form'] = True
        st.session_state['jianying_export_result'] = None
        st.session_state['jianying_export_error'] = None
        st.rerun()



def main():
    """主函数"""
    init_log()
    init_global_state()

    # ===== 显式注册 LLM 提供商（最佳实践）=====
    # 在应用启动时立即注册，确保所有 LLM 功能可用
    if 'llm_providers_registered' not in st.session_state:
        try:
            from app.services.llm.providers import register_all_providers
            register_all_providers()
            st.session_state['llm_providers_registered'] = True
            logger.info("✅ LLM 提供商注册成功")
        except Exception as e:
            logger.error(f"❌ LLM 提供商注册失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            st.error(f"⚠️ LLM 初始化失败: {str(e)}\n\n请检查配置文件和依赖是否正确安装。")
            # 不抛出异常，允许应用继续运行（但 LLM 功能不可用）

    # 检测FFmpeg硬件加速，但只打印一次日志（使用 session_state 持久化）
    if 'hwaccel_logged' not in st.session_state:
        st.session_state['hwaccel_logged'] = False
    
    hwaccel_info = ffmpeg_utils.detect_hardware_acceleration()
    if not st.session_state['hwaccel_logged']:
        if hwaccel_info["available"]:
            logger.info(f"FFmpeg硬件加速检测结果: 可用 | 类型: {hwaccel_info['type']} | 编码器: {hwaccel_info['encoder']} | 独立显卡: {hwaccel_info['is_dedicated_gpu']}")
        else:
            logger.warning(f"FFmpeg硬件加速不可用: {hwaccel_info['message']}, 将使用CPU软件编码")
        st.session_state['hwaccel_logged'] = True

    # 仅初始化基本资源，避免过早地加载依赖PyTorch的资源
    # 检查是否能分解utils.init_resources()为基本资源和高级资源(如依赖PyTorch的资源)
    try:
        utils.init_resources()
    except Exception as e:
        logger.warning(f"资源初始化时出现警告: {e}")

    st.title(f"Jack:blue[AI]:sunglasses: 📽️")
    st.write(tr("Get Help"))

    # 首先渲染不依赖PyTorch的UI部分
    # 渲染基础设置面板
    basic_settings.render_basic_settings(tr)

    # 渲染主面板
    panel = st.columns(3)
    with panel[0]:
        script_settings.render_script_panel(tr)
    with panel[1]:
        audio_settings.render_audio_panel(tr)
    with panel[2]:
        video_settings.render_video_panel(tr)
        subtitle_settings.render_subtitle_panel(tr)

    # 放到最后渲染可能使用PyTorch的部分
    # 渲染系统设置面板
    with panel[2]:
        system_settings.render_system_panel(tr)

    # 放到最后渲染生成按钮和处理逻辑
    render_generate_button()
    render_export_jianying_button()


if __name__ == "__main__":
    main()
