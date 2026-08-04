# -*- coding: utf-8 -*-
"""系统托盘（v2.1 变更 E，FR-13）。

- pystray 独立线程运行，不阻塞 tkinter 主循环；
- 功能：显示/隐藏悬浮窗、开始/暂停识别、退出；
- 悬浮窗隐藏后托盘常驻，随时可恢复（解决"隐藏后找不到"）；
- 注意：托盘线程回调里操作 tkinter 必须经 root.after(0, ...) 调度回主线程。
"""

import logging
import threading

log = logging.getLogger("tray")


def _make_icon_image():
    """生成一个 64×64 托盘图标（PIL 绘制，无需外部素材）。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (64, 64), "#1e1e1e")
    d = ImageDraw.Draw(img)
    # 圆角方块 + 绿色对勾风格：画一个圆 + 字母 A
    d.ellipse((6, 6, 58, 58), fill="#4ade80", outline="#2f9e57", width=2)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 30)
    except Exception:
        font = ImageFont.load_default()
    d.text((21, 14), "A", fill="white", font=font)
    return img


def create_tray(on_toggle_visibility, on_toggle_pause, on_quit):
    """创建托盘图标（回调需自行调度到 tkinter 主线程）。"""
    import pystray

    icon = pystray.Icon("AnswerBot", _make_icon_image(), "AnswerBot 答题助手")
    icon.menu = pystray.Menu(
        pystray.MenuItem("显示/隐藏悬浮窗", lambda: on_toggle_visibility()),
        pystray.MenuItem("开始/暂停识别", lambda: on_toggle_pause()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", lambda: on_quit()),
    )
    return icon


def run_tray(icon) -> threading.Thread:
    """在独立 daemon 线程启动托盘。"""
    t = threading.Thread(target=icon.run, daemon=True, name="tray")
    t.start()
    return t
