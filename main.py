# -*- coding: utf-8 -*-
"""AnswerBot-Screen 入口。

启动流程：DPI-Aware → 日志 → 配置 → 悬浮窗 + 主循环线程 + 手动触发监听。
可直接 `python main.py` 运行；打包见 AnswerBot.spec / README。
"""

import ctypes
import logging
import logging.handlers
import os
import sys
import threading


def setup_dpi_aware():
    """DPI-Aware：必须在任何窗口/抓屏之前调用（坑 1，否则 BitBlt 坐标错位）。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def setup_logging():
    """日志写 logs/answerbot.log（RotatingFileHandler），不打印控制台（坑 10）。
    onefile 打包后 __file__ 是临时目录，日志目录取 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "answerbot.log"),
        maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # 捕获未处理异常也写日志（防静默崩溃）
    def _hook(t, v, tb):
        logging.getLogger("crash").critical("未处理异常", exc_info=(t, v, tb))
        sys.__excepthook__(t, v, tb)
    sys.excepthook = _hook


def main():
    setup_dpi_aware()
    setup_logging()
    log = logging.getLogger("main")
    log.info("=" * 50)
    log.info("AnswerBot-Screen 启动")

    import config as cfg_mod
    cfg = cfg_mod.load()

    # Key 检查提示（不阻塞，悬浮窗状态行可见）
    if not cfg.get("ark_api_key") and not cfg.get("dashscope_api_key"):
        log.warning("未配置任何 API Key（ark_api_key / dashscope_api_key），请先配置 config.json")

    # 导入 GUI 相关（放到 DPI/日志之后）
    from answer_overlay import AnswerOverlay
    from input_listener import InputListener
    from loop import AnswerLoop

    overlay = AnswerOverlay(cfg)

    loop = AnswerLoop(cfg, overlay)

    # 手动触发（FR-11）
    listener = InputListener(cfg, loop.manual_trigger)
    listener.start()

    # 悬浮窗按钮 → loop 控制
    def on_toggle():
        paused = loop.toggle_pause()
        overlay.set_running_state(not paused)

    def on_bind():
        overlay.set_status("请按下要绑定的按键或鼠标键…（Esc 取消）")
        listener.record(lambda binding: _on_record_done(overlay, binding))

    def on_trigger():
        # 保底按钮（v2.3）：点击立即识别；识别耗时数秒，放线程避免卡 UI
        threading.Thread(target=loop.manual_trigger, daemon=True,
                         name="btn-trigger").start()

    overlay.on_toggle = on_toggle
    overlay.on_bind = on_bind
    overlay.on_trigger = on_trigger
    overlay.set_running_state(True)
    overlay.set_status("就绪：监听中（全屏）")

    # 系统托盘（v2.1 变更 E，FR-13）：回调经 root.after 调度回主线程
    tray_icon = None
    try:
        from tray import create_tray, run_tray

        tray_icon = create_tray(
            on_toggle_visibility=overlay.toggle_visibility,
            on_toggle_pause=lambda: overlay.root.after(0, on_toggle),
            on_quit=lambda: overlay.root.after(0, overlay._quit),
        )
        run_tray(tray_icon)
        log.info("系统托盘已启动")
    except Exception as e:
        log.warning("托盘启动失败（不影响主功能）: %s", e)

    # 主循环线程（GUI 主线程不阻塞）
    threading.Thread(target=loop.run, daemon=True, name="answer-loop").start()

    # 主线程进入 tkinter 主循环
    try:
        overlay.run()
    finally:
        loop.stop()
        try:
            listener.stop()
        except Exception:
            pass
        if tray_icon is not None:
            try:
                tray_icon.stop()
            except Exception:
                pass
        log.info("程序退出")


def _on_record_done(overlay, binding):
    """录制完成回调：更新悬浮窗提示。"""
    if binding is None:
        overlay.set_status("已取消绑定")
    else:
        overlay.set_status(f"已绑定：{binding}（按此键立即识别）")


if __name__ == "__main__":
    main()
