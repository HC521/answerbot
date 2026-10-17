# -*- coding: utf-8 -*-
"""悬浮窗模块（FR-06 / FR-07）：tkinter 置顶半透明无边框小窗。

- overrideredirect(True) 无边框；-topmost 置顶；-alpha 半透明；
- 布局：题号 + 模型切换 + 按钮行 + 答案 + 题目摘要 + 状态行（v2.5 新增模型切换）；
- 拖拽移动，释放时保存位置到 config（重启记忆）；
- v2.5：位置越界自动召回——保存的位置若落在当前屏幕外（换过显示器布局、
  双屏改单屏），启动时自动移回主屏右上角，否则窗口"消失"只剩托盘；
- 全局热键 Ctrl+Alt+H 隐藏/恢复（keyboard 库；无权限自动降级为窗口「隐藏」按钮）；
- 跨线程安全：loop 线程通过 root.after(0, fn) 调度 UI 更新。
"""

import logging
import tkinter as tk

import config as cfg_mod

log = logging.getLogger("overlay")


class AnswerOverlay:
    """答案悬浮窗。on_toggle / on_bind / on_trigger 为回调（由 main/loop 注入）。"""

    WIDTH, HEIGHT = 420, 200

    def __init__(self, cfg: dict, on_toggle=None, on_bind=None, on_trigger=None,
                 on_provider_change=None):
        self.cfg = cfg
        self.on_toggle = on_toggle or (lambda: None)   # 开始/暂停回调
        self.on_bind = on_bind or (lambda: None)       # 绑定按键回调
        self.on_trigger = on_trigger or (lambda: None)  # 保底按钮回调（v2.3）
        # v2.5：切换首选模型提供方；返回 True=成功（main 注入，内部调用 loop.set_provider）
        self.on_provider_change = on_provider_change or (lambda pid: False)
        self.running = True                            # 循环运行状态（供按钮显示）
        self.visible = True
        self._drag = None                              # 拖拽偏移

        self.root = tk.Tk()
        self.root.title("AnswerBot")
        self.root.overrideredirect(True)               # 无边框
        self.root.attributes("-topmost", True)         # 置顶
        self.root.attributes("-alpha", cfg.get("overlay_alpha", 0.85))
        self.root.configure(bg="#1e1e1e")

        self._build_widgets()
        self._place_default()
        self._bind_drag()
        self._register_hotkey()
        self._start_lift_loop()
        self.refresh_providers()          # v2.5：模型菜单初始态

        # 关窗即退出整个程序
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

    # ------------------------------------------------------------- 置顶强化
    def _start_lift_loop(self):
        """每 5s lift 一次，防止被考试系统窗口盖住（v2.1 变更 D）。"""
        self.root.after(5000, self._periodic_lift)

    def _periodic_lift(self):
        try:
            if self.visible:
                self.root.lift()
        except Exception:
            pass
        self.root.after(5000, self._periodic_lift)

    # ------------------------------------------------------------------ UI
    def _build_widgets(self):
        self.root.geometry(f"{self.WIDTH}x{self.HEIGHT}")

        # 顶栏：题号 + 按钮行
        top = tk.Frame(self.root, bg="#1e1e1e")
        top.pack(fill="x", padx=8, pady=(6, 0))
        self.no_label = tk.Label(top, text="第 0 题", bg="#1e1e1e", fg="#888888",
                                 font=("Microsoft YaHei UI", 9))
        self.no_label.pack(side="left")

        # v2.5：模型切换（当前首选模型 → 点开选 DeepSeek/千问/豆包）
        self.provider_btn = tk.Menubutton(top, text="模型", font=("Microsoft YaHei UI", 8),
                                          bd=0, bg="#2a3a2a", fg="#8ee6a0",
                                          activebackground="#3a4a3a", activeforeground="white",
                                          padx=6, pady=1, relief="flat", indicatoron=False,
                                          cursor="hand2")
        self.provider_menu = tk.Menu(self.provider_btn, tearoff=0,
                                     font=("Microsoft YaHei UI", 9))
        self.provider_btn.config(menu=self.provider_menu)
        self.provider_btn.pack(side="left", padx=(8, 2))

        # v2.3 保底按钮：点击立即识别（放最右，显眼）
        self.btn_trigger = tk.Button(top, text="保底", command=self._on_trigger_click,
                                     font=("Microsoft YaHei UI", 8), bd=0,
                                     bg="#4ade80", fg="#1e1e1e", padx=6, pady=1,
                                     activebackground="#22c55e", activeforeground="white")
        self.btn_trigger.pack(side="right", padx=2)
        self.btn_hide = tk.Button(top, text="隐藏", command=self.toggle_visibility,
                                  font=("Microsoft YaHei UI", 8), bd=0,
                                  bg="#3a3a3a", fg="#cccccc", padx=6, pady=1,
                                  activebackground="#555555", activeforeground="white")
        self.btn_hide.pack(side="right", padx=2)
        self.btn_toggle = tk.Button(top, text="暂停", command=self._on_toggle_click,
                                    font=("Microsoft YaHei UI", 8), bd=0,
                                    bg="#3a3a3a", fg="#cccccc", padx=6, pady=1,
                                    activebackground="#555555", activeforeground="white")
        self.btn_toggle.pack(side="right", padx=2)
        self.btn_bind = tk.Button(top, text="绑定按键", command=self._on_bind_click,
                                  font=("Microsoft YaHei UI", 8), bd=0,
                                  bg="#3a3a3a", fg="#cccccc", padx=6, pady=1,
                                  activebackground="#555555", activeforeground="white")
        self.btn_bind.pack(side="right", padx=2)

        # 答案（大号粗体绿色）
        self.answer_label = tk.Label(self.root, text="等待题目…", bg="#1e1e1e",
                                     fg="#4ade80", font=("Microsoft YaHei UI", 26, "bold"),
                                     anchor="w")
        self.answer_label.pack(fill="x", padx=12, pady=(6, 0))

        # 题目摘要（≤120 字，自动换行）
        self.summary_label = tk.Label(self.root, text="", bg="#1e1e1e", fg="#bbbbbb",
                                      font=("Microsoft YaHei UI", 9), anchor="w",
                                      justify="left", wraplength=self.WIDTH - 24)
        self.summary_label.pack(fill="x", padx=12)

        # 状态行（识别中/失败原因等）
        self.status_label = tk.Label(self.root, text="就绪", bg="#1e1e1e", fg="#666666",
                                     font=("Microsoft YaHei UI", 8), anchor="w")
        self.status_label.pack(fill="x", padx=12, pady=(0, 4))

    def _place_default(self):
        """初始位置：config 记忆位置；若越界（换过显示器布局）自动召回主屏（v2.5）。"""
        raw = self.cfg.get("overlay_pos") or [None, None]
        x, y = (raw + [None, None])[:2] if isinstance(raw, (list, tuple)) else (None, None)
        if x is None or y is None:
            x, y = self._default_pos()
        else:
            x, y = int(x), int(y)
            if not self._is_pos_visible(x, y):
                log.warning("保存的悬浮窗位置 (%d, %d) 已不在当前屏幕内"
                            "（可能换过显示器/拔掉副屏），自动移回主屏", x, y)
                x, y = self._default_pos()
        self.root.geometry(f"+{x}+{y}")
        # 确保窗口确实被映射出来（overrideredirect 窗口在某些环境下需要显式 deiconify）
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass

    # ------------------------------------------------------- 位置校验（v2.5）
    def _default_pos(self) -> tuple[int, int]:
        """主屏（屏幕 1）右上角。"""
        sw = self.root.winfo_screenwidth()
        return max(0, sw - self.WIDTH - 20), 40

    def _virtual_rect(self) -> tuple[int, int, int, int]:
        """虚拟桌面矩形（所有显示器合并）。取不到时退回主屏尺寸。"""
        try:
            from screen_capture import virtual_screen_rect
            return virtual_screen_rect()
        except Exception:
            return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _is_pos_visible(self, x: int, y: int) -> bool:
        """窗口是否至少有 60% 面积落在当前虚拟桌面内。"""
        vx, vy, vw, vh = self._virtual_rect()
        vis_w = min(x + self.WIDTH, vx + vw) - max(x, vx)
        vis_h = min(y + self.HEIGHT, vy + vh) - max(y, vy)
        return vis_w >= self.WIDTH * 0.6 and vis_h >= self.HEIGHT * 0.6

    def reset_position(self):
        """把窗口召回主屏右上角并持久化（v2.5，托盘菜单/用户手动恢复用）。"""
        def _do():
            x, y = self._default_pos()
            self.root.geometry(f"+{int(x)}+{int(y)}")
            try:
                self.root.deiconify()
                self.root.lift()
            except Exception:
                pass
            self.visible = True
            try:
                cfg_mod.set("overlay_pos", [int(x), int(y)])
            except Exception as e:
                log.warning("保存悬浮窗位置失败: %s", e)
            self.set_status(f"窗口已移回主屏 ({int(x)}, {int(y)})")
            log.info("悬浮窗位置已重置为 (%d, %d)", int(x), int(y))

        try:
            self.root.after(0, _do)
        except Exception as e:
            log.warning("重置悬浮窗位置失败: %s", e)

    # ------------------------------------------------------------- 拖拽
    def _bind_drag(self):
        # 拖拽绑定在 root（toplevel），按钮上的点击会冒泡到这里，
        # 由 _drag_start 判断跳过——不能 bind "<Button-1>" 到按钮并 return "break"，
        # 那会阻断按钮自身的 class 绑定导致按钮失效（坑：按钮点不动）。
        self.root.bind("<Button-1>", self._drag_start)
        self.root.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<ButtonRelease-1>", self._drag_end)
        # 右键菜单：无边框窗口没有系统 X，提供退出/隐藏/暂停入口
        self.root.bind("<Button-3>", self._show_menu)

    def _drag_start(self, event):
        if event.widget in (self.btn_hide, self.btn_toggle, self.btn_bind,
                            self.btn_trigger, self.provider_btn):
            return  # 按钮区域不拖拽（不阻断事件，按钮才能正常响应）
        self._drag = (event.x_root - self.root.winfo_x(),
                      event.y_root - self.root.winfo_y())

    def _drag_move(self, event):
        if self._drag:
            x = event.x_root - self._drag[0]
            y = event.y_root - self._drag[1]
            self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, _event):
        if self._drag:
            self._drag = None
            # 记忆位置（重启恢复）
            try:
                cfg_mod.set("overlay_pos",
                            [self.root.winfo_x(), self.root.winfo_y()])
            except Exception as e:
                log.warning("保存悬浮窗位置失败: %s", e)

    # ------------------------------------------------------------- 热键
    def _register_hotkey(self):
        """全局热键隐藏/恢复；无权限/无库时降级为窗口按钮（不崩溃）。"""
        try:
            import keyboard
            keyboard.add_hotkey(self.cfg.get("hotkey", "ctrl+alt+h"),
                                self.toggle_visibility)
            log.info("全局热键已注册: %s", self.cfg.get("hotkey"))
        except Exception as e:
            log.warning("全局热键注册失败（已降级为窗口「隐藏」按钮）: %s", e)

    def _show_menu(self, event):
        """右键菜单：无边框窗口的退出/控制入口（v2.5 增加模型切换与窗口找回）。"""
        from ai_client import provider_label, provider_status

        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="隐藏/显示  (Ctrl+Alt+H)", command=self.toggle_visibility)
        m.add_command(label="暂停/开始", command=self._on_toggle_click)
        m.add_separator()
        cur = self.cfg.get("ai_provider") or "deepseek"
        sub = tk.Menu(m, tearoff=0)
        for st in provider_status(self.cfg):
            text = st["label"] + ("" if st["configured"] else "（未配置）")
            if st["id"] == cur:
                text = "✓ " + text
            sub.add_command(label=text,
                            command=lambda pid=st["id"]: self._on_provider_select(pid))
        m.add_cascade(label=f"模型：{provider_label(cur)}", menu=sub)
        m.add_command(label="窗口移回主屏", command=self.reset_position)
        m.add_separator()
        m.add_command(label="退出程序", command=self._quit)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def toggle_visibility(self):
        """隐藏/恢复悬浮窗。可被 keyboard 热键线程/按钮主线程调用（内部调度到主线程）。"""
        self.root.after(0, self._toggle_ui)

    def _toggle_ui(self):
        """实际切换逻辑（必须在 tkinter 主线程执行）。"""
        if self.visible:
            self.root.withdraw()
        else:
            self.root.deiconify()
        self.visible = not self.visible

    # ------------------------------------------------------------- 对外接口
    def show(self, question_no: int, answer: str, question_summary: str = "",
             status: str = ""):
        """显示一题结果。可由任意线程调用（内部调度到主线程）。"""
        self.root.after(0, lambda: self._show_ui(question_no, answer,
                                                 question_summary, status))

    def set_status(self, text: str):
        """更新状态行（识别中/失败原因/录制提示等）。"""
        self.root.after(0, lambda: self.status_label.config(text=text))

    def _show_ui(self, question_no, answer, question_summary, status):
        self.no_label.config(text=f"第 {question_no} 题")
        self.answer_label.config(text=answer or "（识别失败）")
        summary = (question_summary or "")[:120]  # ≤120 字，超长截断
        self.summary_label.config(text=summary)
        if status:
            self.status_label.config(text=status)
        # 显示答案时置顶最前（v2.1 变更 D：P4）
        try:
            self.root.lift()
        except Exception:
            pass
        # 可选：复制答案到剪贴板
        if self.cfg.get("copy_to_clipboard") and answer:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(answer)
            except Exception as e:
                log.warning("复制到剪贴板失败: %s", e)

    def get_rect_pixels(self):
        """悬浮窗在全屏合并图坐标系下的像素矩形 (x, y, w, h)。

        隐藏时返回 None（不参与差分排除）；可用 config overlay_rect 手动覆盖。
        """
        if not self.visible:
            return None
        rect = self.cfg.get("overlay_rect")
        if rect:
            return tuple(int(v) for v in rect)
        try:
            from screen_capture import monitor_origin

            ox, oy = monitor_origin()
            x = self.root.winfo_rootx() - ox
            y = self.root.winfo_rooty() - oy
            w = self.root.winfo_width() or self.WIDTH
            h = self.root.winfo_height() or self.HEIGHT
            return (x, y, w, h)
        except Exception as e:
            log.warning("计算悬浮窗矩形失败: %s", e)
            return None

    # ------------------------------------------------------------- 按钮回调
    def _on_toggle_click(self):
        self.running = not self.running
        self.btn_toggle.config(text="开始" if not self.running else "暂停")
        self.on_toggle()

    def _on_bind_click(self):
        self.on_bind()

    def _on_trigger_click(self):
        """保底按钮：立即触发一次识别（v2.3）。"""
        self.on_trigger()

    # --------------------------------------------------- 模型切换（v2.5）
    def refresh_providers(self):
        """重建模型菜单（配置变化后调用；也可从任意线程调）。"""
        try:
            self.root.after(0, self._refresh_providers_ui)
        except Exception:
            self._refresh_providers_ui()

    def _refresh_providers_ui(self):
        from ai_client import is_provider_configured, provider_label, provider_status

        cur = self.cfg.get("ai_provider") or "deepseek"
        self.provider_menu.delete(0, "end")
        for st in provider_status(self.cfg):
            text = st["label"]
            if not st["configured"]:
                text += "（未配置）"
            if st["id"] == cur:
                text = "✓ " + text
            self.provider_menu.add_command(
                label=text, command=lambda pid=st["id"]: self._on_provider_select(pid))
        self.provider_btn.config(text=f"{provider_label(cur)} ▾")
        # 当前首选没配 Key 时标黄，提醒先配置
        self.provider_btn.config(
            fg="#8ee6a0" if is_provider_configured(self.cfg, cur) else "#e6b450")

    def _on_provider_select(self, pid: str):
        from ai_client import provider_label

        if self.on_provider_change(pid):
            self._refresh_providers_ui()
            self.set_status(f"已切换到 {provider_label(pid)}")
        else:
            self.set_status(f"{provider_label(pid)} 未配置 Key（可在 config.json 填写或重开程序配置）")

    def set_running_state(self, running: bool):
        """loop 线程同步运行状态（保证按钮文本一致）。"""
        self.running = running
        self.root.after(0, lambda: self.btn_toggle.config(
            text="暂停" if running else "开始"))

    def _quit(self):
        try:
            import keyboard
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        """进入 tkinter 主循环（必须在主线程调用）。"""
        self.root.mainloop()


if __name__ == "__main__":
    # 自测：python answer_overlay.py （显示 5 秒演示窗口）
    import threading
    import time

    c = cfg_mod.load()
    ov = AnswerOverlay(c)
    threading.Thread(target=lambda: (time.sleep(2),
                                     ov.show(3, "B", "示例题目：1+1 等于几？",
                                             "识别中…"),
                                     time.sleep(2),
                                     ov.set_status("演示结束"),
                                     time.sleep(1),
                                     ov._quit()),
                     daemon=True).start()
    ov.run()
