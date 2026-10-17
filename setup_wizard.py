# -*- coding: utf-8 -*-
"""API Key 图形配置（v2.6：首次向导 + 运行时设置，统一同一套表单）。

两个入口共用同一个 `KeyForm`：
1. **首次运行向导**：程序启动时没有任何 Key → 独立窗口，填完「保存并启动」；
2. **运行时设置**：悬浮窗右上角「⚙」/ 右键菜单 / 托盘 → 「设置 API Key…」，
   随时粘贴/更换 Key，**不用再手动改 config.json**。

表单设计（面向小白）：
- 三个标签页：DeepSeek / 千问 / 豆包，已配置的标签带 ✓；
- 每个标签：API Key 输入框（可勾「显示」核对）+ 模型（已预填默认值）+ 「测试连接」；
- 留空表示不修改该项；保存后立即生效（悬浮窗模型菜单同步刷新）；
- Key 只写本机 `config.json`（已被 .gitignore，不会上传/入库）。

用法：python setup_wizard.py  # 单独打开向导自测
"""

import logging
import threading
import tkinter as tk
from tkinter import ttk

import config as cfg_mod
from ai_client import (DEFAULT_MODELS, PROVIDERS, configured_providers,
                       is_provider_configured, provider_label, provider_order)

log = logging.getLogger("setup")

FONT = "Microsoft YaHei UI"
ORDER = ("deepseek", "qwen", "doubao")

TIPS = {
    "deepseek": "推荐：DeepSeek 视觉模型，1~2 秒出答案。\n"
                "Key 获取：platform.deepseek.com → API Keys（模型默认 deepseek-flash）",
    "qwen": "阿里云 DashScope，中文题稳定。\n"
            "Key 获取：dashscope.console.aliyun.com → API-KEY 管理（模型默认 qwen-vl-max）",
    "doubao": "火山方舟豆包：Key 和「接入点 ID」都要填（模型形如 ep-xxxxxxxx）。\n"
              "Key 获取：console.volcengine.com/ark → API Key 管理",
}
BG = "#f5f5f5"


# ---------------------------------------------------------------------------
# 三提供方共用的表单
# ---------------------------------------------------------------------------
class KeyForm(ttk.Frame):
    """标签页式 Key 表单。collect() 返回要写入 config 的字段。"""

    def __init__(self, master, cfg: dict):
        super().__init__(master)
        self.cfg = cfg
        self.fields = {}
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=8, pady=8)

        primary = (cfg.get("ai_provider") or "deepseek")
        for pid in ORDER:
            self._build_tab(pid)
        # 默认停在当前首选提供方
        if primary in ORDER:
            self._notebook.select(self.fields[primary]["tab"])

    def _build_tab(self, pid: str):
        meta = PROVIDERS[pid]
        tab = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(tab, text=self._tab_text(pid))

        tk.Label(tab, text=TIPS[pid], bg=BG, fg="#555555", font=(FONT, 9),
                 justify="left", anchor="w", wraplength=580).pack(
            fill="x", padx=12, pady=(10, 8))

        # Key 行
        kf = tk.Frame(tab, bg=BG)
        kf.pack(fill="x", padx=12, pady=3)
        tk.Label(kf, text="API Key", bg=BG, width=8, anchor="w",
                 font=(FONT, 10)).pack(side="left")
        key_var = tk.StringVar(value=self.cfg.get(meta["key_field"], "") or "")
        entry = tk.Entry(kf, textvariable=key_var, show="*", width=52,
                         font=("Consolas", 10))
        entry.pack(side="left", ipady=2)
        show_var = tk.BooleanVar(value=False)
        tk.Checkbutton(kf, text="显示", variable=show_var, bg=BG, font=(FONT, 9),
                       command=lambda e=entry, v=show_var: e.config(
                           show="" if v.get() else "*")).pack(side="left", padx=(8, 0))

        # 模型行
        mf = tk.Frame(tab, bg=BG)
        mf.pack(fill="x", padx=12, pady=3)
        tk.Label(mf, text="模型", bg=BG, width=8, anchor="w",
                 font=(FONT, 10)).pack(side="left")
        model_var = tk.StringVar(value=(self.cfg.get(meta["model_field"], "")
                                        or DEFAULT_MODELS.get(pid, "")))
        tk.Entry(mf, textvariable=model_var, width=34, font=("Consolas", 10)).pack(
            side="left", ipady=2)
        tk.Label(mf, text="（一般不用改）", bg=BG, fg="#999999",
                 font=(FONT, 9)).pack(side="left", padx=(6, 0))

        # 测试行
        tf = tk.Frame(tab, bg=BG)
        tf.pack(fill="x", padx=12, pady=(8, 4))
        btn = ttk.Button(tf, text="测试连接",
                         command=lambda p=pid: self._test(p))
        btn.pack(side="left")
        status = tk.Label(tf, text="", bg=BG, fg="#666666", font=(FONT, 9),
                          anchor="w", justify="left", wraplength=380)
        status.pack(side="left", padx=(10, 0))

        self.fields[pid] = {"key": key_var, "model": model_var, "show": show_var,
                            "entry": entry, "status": status, "btn": btn, "tab": tab}

    def _tab_text(self, pid: str) -> str:
        return provider_label(pid) + ("  ✓" if is_provider_configured(self.cfg, pid) else "")

    def refresh_tabs(self):
        for pid in ORDER:
            self._notebook.tab(self.fields[pid]["tab"], text=self._tab_text(pid))

    # -- 测试 ---------------------------------------------------------------
    def _test(self, pid: str):
        meta = PROVIDERS[pid]
        f = self.fields[pid]
        key = f["key"].get().strip()
        model = f["model"].get().strip() or DEFAULT_MODELS.get(pid, "")
        if not key:
            self._show(pid, "请先粘贴 API Key", ok=False)
            return
        self._show(pid, "正在测试连接…")
        f["btn"].config(state="disabled")

        def _worker():
            from ai_client import test_connection
            ok, msg = test_connection(pid, key, model)
            try:
                self.after(0, lambda: self._test_done(pid, ok, msg))
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name=f"test-{pid}").start()

    def _test_done(self, pid: str, ok: bool, msg: str):
        self.fields[pid]["btn"].config(state="normal")
        self._show(pid, ("✅ " if ok else "❌ ") + msg, ok=ok)

    def _show(self, pid: str, text: str, ok: bool | None = None):
        color = "#666666" if ok is None else ("#1a7f37" if ok else "#c62828")
        self.fields[pid]["status"].config(text=text, fg=color)

    # -- 收集 ---------------------------------------------------------------
    def collect(self) -> dict:
        """返回要写入 config 的字段（Key 为空表示不修改该提供方）。"""
        updates = {}
        for pid in ORDER:
            meta = PROVIDERS[pid]
            key = self.fields[pid]["key"].get().strip()
            model = self.fields[pid]["model"].get().strip() or DEFAULT_MODELS.get(pid, "")
            if key:
                updates[meta["key_field"]] = key
                updates[meta["model_field"]] = model
        return updates

    def simulated_config(self, updates: dict) -> dict:
        c = dict(self.cfg)
        c.update(updates)
        return c


def _apply_and_save(cfg: dict, updates: dict) -> tuple[bool, str]:
    """把表单内容写入 cfg 并落盘；必要时自动把首选切到已配置的提供方。"""
    if not updates:
        return False, "请至少在一个标签页里粘贴 API Key"
    cfg.update(updates)
    if not is_provider_configured(cfg, cfg.get("ai_provider")):
        avail = configured_providers(cfg) or provider_order(cfg)
        if avail:
            cfg["ai_provider"] = avail[0]
    try:
        cfg_mod.save(cfg)
    except Exception as e:
        return False, f"保存失败：{e}"
    log.info("API Key 已更新：已配置=%s 首选=%s",
             configured_providers(cfg), cfg.get("ai_provider"))
    return True, f"已保存（当前模型：{provider_label(cfg.get('ai_provider'))}）"


# ---------------------------------------------------------------------------
# 运行时设置对话框（悬浮窗 ⚙ / 右键 / 托盘打开）
# ---------------------------------------------------------------------------
class KeySettingsDialog(tk.Toplevel):
    """不重启程序、直接在界面里粘贴/更换 Key。"""

    def __init__(self, parent, cfg: dict, on_saved=None, on_closed=None):
        super().__init__(parent)
        self.cfg = cfg
        self.on_saved = on_saved
        self.on_closed = on_closed
        self._closed_once = False
        self.title("设置 API Key")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.attributes("-topmost", True)

        w, h = 660, 430
        # 居中于屏幕（不贴着悬浮窗放，避免和置顶悬浮窗互相遮挡）
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.geometry(f"{w}x{h}+{x}+{y}")

        tk.Label(self, text="粘贴 API Key（留空表示不修改）", bg=BG,
                 font=(FONT, 12, "bold"), anchor="w").pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(self, text="填好可点「测试连接」确认，再点保存立即生效；Key 只存本机 config.json。",
                 bg=BG, fg="#666666", font=(FONT, 9), anchor="w").pack(fill="x", padx=16)

        self.form = KeyForm(self, cfg)
        self.form.pack(fill="both", expand=True, padx=6, pady=4)

        self.status = tk.Label(self, text="", bg=BG, fg="#666666", font=(FONT, 9),
                               anchor="w")
        self.status.pack(fill="x", padx=16)

        bf = tk.Frame(self, bg=BG)
        bf.pack(fill="x", padx=16, pady=(6, 12))
        ttk.Button(bf, text="保存", command=self._save).pack(side="right")
        ttk.Button(bf, text="取消", command=self.destroy).pack(side="right", padx=8)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.focus_force()

    def destroy(self):
        """关闭时通知调用方（恢复悬浮窗 lift 行为）。幂等。"""
        if self._closed_once:
            return
        self._closed_once = True
        try:
            if self.on_closed:
                self.on_closed()
        except Exception as e:
            log.warning("对话框关闭回调异常: %s", e)
        try:
            super().destroy()
        except Exception:
            pass

    def _save(self):
        updates = self.form.collect()
        ok, msg = _apply_and_save(self.cfg, updates)
        self.status.config(text=("✅ " if ok else "❌ ") + msg,
                           fg="#1a7f37" if ok else "#c62828")
        if ok:
            if self.on_saved:
                try:
                    self.on_saved(self.cfg)
                except Exception as e:
                    log.warning("保存后回调异常: %s", e)
            self.after(350, self.destroy)


def open_key_settings(parent_root, cfg: dict, on_saved=None, on_closed=None):
    """打开「设置 API Key」对话框（调用方保证在 tkinter 主线程）。"""
    try:
        return KeySettingsDialog(parent_root, cfg, on_saved=on_saved,
                                 on_closed=on_closed)
    except Exception as e:
        log.exception("打开 Key 设置失败: %s", e)
        return None


# ---------------------------------------------------------------------------
# 首次运行向导
# ---------------------------------------------------------------------------
class SetupWizard:
    """首次配置向导：独立 Tk root，close 后返回 cfg（取消则 None）。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.result = None
        self.root = tk.Tk()
        self.root.title("AnswerBot 首次配置")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)

        w, h = 680, 425
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

        tk.Label(self.root, text="第一次使用：把 API Key 粘贴进来", bg=BG,
                 font=(FONT, 14, "bold"), anchor="w").pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(self.root,
                 text="推荐 DeepSeek（1~2 秒/题）。Key 只存本机 config.json，不会上传。",
                 bg=BG, fg="#777777", font=(FONT, 9), anchor="w").pack(fill="x", padx=18)

        self.form = KeyForm(self.root, cfg)
        self.form.pack(fill="both", expand=True, padx=8, pady=4)

        self.status = tk.Label(self.root, text="", bg=BG, fg="#666666",
                               font=(FONT, 9), anchor="w")
        self.status.pack(fill="x", padx=18)

        bf = tk.Frame(self.root, bg=BG)
        bf.pack(fill="x", padx=18, pady=(4, 12))
        ttk.Button(bf, text="保存并启动 ▶", command=self._save).pack(side="right")
        ttk.Button(bf, text="稍后配置", command=self._skip).pack(side="right", padx=8)

    def _save(self):
        updates = self.form.collect()
        ok, msg = _apply_and_save(self.cfg, updates)
        self.status.config(text=("✅ " if ok else "❌ ") + msg,
                           fg="#1a7f37" if ok else "#c62828")
        if ok:
            self.result = self.cfg
            self.root.after(350, self.root.destroy)

    def _skip(self):
        log.info("用户跳过首次配置")
        self.result = None
        self.root.destroy()

    def run(self) -> dict | None:
        self.root.mainloop()
        return self.result


def run_setup_wizard(cfg: dict) -> dict | None:
    """首次向导（异常不挡主程序）。"""
    try:
        return SetupWizard(cfg).run()
    except Exception as e:
        log.exception("配置向导异常（已跳过）: %s", e)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    c = cfg_mod.load()
    new_cfg = run_setup_wizard(c)
    print("向导返回:", "已保存" if new_cfg else "跳过")
    if new_cfg:
        print("首选:", new_cfg.get("ai_provider"),
              "已配置:", configured_providers(new_cfg))
