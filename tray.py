# -*- coding: utf-8 -*-
"""系统托盘（v2.1 变更 E，FR-13；v2.5 增加模型切换与窗口找回）。

- pystray 独立线程运行，不阻塞 tkinter 主循环；
- 功能：显示/隐藏悬浮窗、开始/暂停识别、模型切换（单选）、窗口移回主屏、退出；
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


def create_tray(on_toggle_visibility, on_toggle_pause, on_quit,
                on_reset_position=None, provider_items=None,
                on_select_provider=None, current_provider=None,
                on_open_settings=None):
    """创建托盘图标。

    回调需自行调度到 tkinter 主线程。
    provider_items: [{"id","label","configured"}]；on_select_provider(pid)；
    current_provider: 返回当前首选 provider id 的 callable（用于单选勾选）；
    on_open_settings: 打开「设置 API Key」对话框（v2.6）。
    """
    import pystray

    icon = pystray.Icon("AnswerBot", _make_icon_image(), "AnswerBot 答题助手")

    items = [
        pystray.MenuItem("显示/隐藏悬浮窗", lambda: on_toggle_visibility()),
        pystray.MenuItem("开始/暂停识别", lambda: on_toggle_pause()),
    ]

    if provider_items and on_select_provider:
        def _checked(pid):
            def _fn(item):
                try:
                    return bool(current_provider and current_provider() == pid)
                except Exception:
                    return False
            return _fn

        sub_items = []
        for it in provider_items:
            sub_items.append(pystray.MenuItem(
                it["label"] + ("" if it.get("configured") else "（未配置）"),
                (lambda pid=it["id"]: on_select_provider(pid)),
                checked=_checked(it["id"]),
                radio=True,
                enabled=bool(it.get("configured", True)),
            ))
        items.append(pystray.MenuItem("模型切换", pystray.Menu(*sub_items)))

    if on_reset_position:
        items.append(pystray.MenuItem("窗口移回主屏", lambda: on_reset_position()))

    if on_open_settings:
        items.append(pystray.MenuItem("⚙ 设置 API Key…", lambda: on_open_settings()))

    items.append(pystray.Menu.SEPARATOR)
    items.append(pystray.MenuItem("退出", lambda: on_quit()))
    icon.menu = pystray.Menu(*items)
    return icon


def run_tray(icon) -> threading.Thread:
    """在独立 daemon 线程启动托盘。"""
    t = threading.Thread(target=icon.run, daemon=True, name="tray")
    t.start()
    return t
