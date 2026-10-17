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

    # —— v2.5：首次运行（没有任何可用 Key）→ 先弹配置向导 ——
    from ai_client import configured_providers, provider_label, provider_status

    if not configured_providers(cfg):
        log.info("未检测到可用的模型 Key，进入首次配置向导")
        try:
            from setup_wizard import run_setup_wizard

            if run_setup_wizard(cfg):
                cfg = cfg_mod.load()
                log.info("首次配置已完成：首选模型 = %s", provider_label(cfg.get("ai_provider")))
            else:
                log.warning("未完成配置：识别时会在状态行提示缺少 Key")
        except Exception as e:
            log.exception("配置向导启动失败（已跳过）: %s", e)

    # 导入 GUI 相关（放到 DPI/日志之后）
    from answer_overlay import AnswerOverlay
    from input_listener import InputListener
    from loop import AnswerLoop

    overlay = AnswerOverlay(cfg)

    loop = AnswerLoop(cfg, overlay)

    # —— v2.5：模型切换（悬浮窗「模型」菜单 / 托盘 / 右键菜单共用） ——
    def _switch_provider(pid):
        try:
            ok = loop.set_provider(pid)
        except Exception as e:
            log.warning("切换模型失败: %s", e)
            ok = False
        if ok:
            overlay.refresh_providers()
        return ok

    overlay.on_provider_change = _switch_provider
    overlay.refresh_providers()

    # —— v2.6：界面里直接粘贴/更换 API Key（悬浮窗 ⚙ / 模型菜单 / 右键 / 托盘）——
    def on_open_settings():
        from setup_wizard import open_key_settings

        def _saved(_updated):
            overlay.refresh_providers()
            overlay.set_status(
                f"Key 已保存 · 当前模型 {provider_label(cfg.get('ai_provider'))}")

        def _closed():
            overlay.modal_open = False
            try:
                overlay.root.attributes("-topmost", True)   # 恢复悬浮窗置顶
            except Exception:
                pass

        # 打开对话框期间：暂停悬浮窗 lift，并临时取消其置顶，避免盖住对话框
        overlay.modal_open = True
        try:
            overlay.root.attributes("-topmost", False)
        except Exception:
            pass
        dlg = open_key_settings(overlay.root, cfg, on_saved=_saved, on_closed=_closed)
        if dlg is None:
            _closed()

    overlay.on_open_settings = on_open_settings

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
    overlay.set_status(f"就绪：监听中（全屏）· {provider_label(cfg.get('ai_provider'))}")

    # 系统托盘（v2.1 变更 E，FR-13）：回调经 root.after 调度回主线程
    tray_icon = None
    try:
        from tray import create_tray, run_tray

        tray_icon = create_tray(
            on_toggle_visibility=overlay.toggle_visibility,
            on_toggle_pause=lambda: overlay.root.after(0, on_toggle),
            on_quit=lambda: overlay.root.after(0, overlay._quit),
            # v2.5：模型切换 + 窗口找回（单屏下窗口跑到屏外的救命入口）
            on_reset_position=overlay.reset_position,
            provider_items=provider_status(cfg),
            on_select_provider=lambda pid: overlay.root.after(
                0, lambda: overlay._on_provider_select(pid)),
            current_provider=lambda: cfg.get("ai_provider") or "deepseek",
            on_open_settings=lambda: overlay.root.after(0, on_open_settings),
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
