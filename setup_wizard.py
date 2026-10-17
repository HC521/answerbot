# -*- coding: utf-8 -*-
"""首次运行配置向导（v2.5）。

程序启动时若检测到「没有任何模型提供方配置了 API Key」，先弹出本向导：
选提供方（DeepSeek / 千问 / 豆包）→ 填 Key（可选改模型）→ 测试连接 → 保存并启动。

设计要点：
- Key 只写入本机 `config.json`（已在 .gitignore，不会入库/上传）；
- 向导是普通窗口（有标题栏），方便第一次使用的人和同事填写；
- 「稍后配置」可直接跳过，程序照常启动（托盘/悬浮窗在，只是识别时提示未配置）；
- 独立 tk.Tk()，向导销毁后 main 再创建悬浮窗，避免两个 Tk root 冲突。

用法：python setup_wizard.py  # 单独打开向导自测
"""

import logging
import threading
import tkinter as tk
from tkinter import ttk

import config as cfg_mod
from ai_client import DEFAULT_MODELS, PROVIDERS, provider_label

log = logging.getLogger("setup")

FONT = "Microsoft YaHei UI"

TIPS = {
    "deepseek": "推荐：DeepSeek 视觉模型（1~2 秒/题，支持图片识别多模态）。\n"
                "Key 获取：platform.deepseek.com → API Keys。模型默认 deepseek-flash。",
    "qwen": "阿里云 DashScope（qwen-vl-max），识别稳定、中文题友好。\n"
            "Key 获取：dashscope.console.aliyun.com → API-KEY 管理。",
    "doubao": "火山方舟豆包：Key 与「接入点 ID」都要填（模型填 ep-xxxx）。\n"
              "Key 获取：console.volcengine.com/ark → API Key 管理。",
}


class SetupWizard:
    """首次配置向导。run() 阻塞到窗口关闭，返回更新后的 cfg（取消则 None）。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.result = None
        self.root = tk.Tk()
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
        r = self.root
        r.title("AnswerBot 首次配置")
        r.resizable(False, False)
        r.attributes("-topmost", True)
        r.configure(bg="#f5f5f5")

        w, h = 640, 380
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

        tk.Label(r, text="首次使用：选择模型并填写 API Key", bg="#f5f5f5",
                 font=(FONT, 14, "bold"), anchor="w").pack(fill="x", padx=18, pady=(16, 2))
        tk.Label(r, text="Key 只保存在本机 config.json（已在 .gitignore，不会上传或提交到代码库）",
                 bg="#f5f5f5", fg="#777777", font=(FONT, 9), anchor="w").pack(fill="x", padx=18)

        # —— 提供方 ——
        pf = tk.Frame(r, bg="#f5f5f5")
        pf.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(pf, text="模型提供方", bg="#f5f5f5", width=9, anchor="w",
                 font=(FONT, 10)).pack(side="left")
        self.provider_var = tk.StringVar(value=(self.cfg.get("ai_provider") or "deepseek"))
        for pid in ("deepseek", "qwen", "doubao"):
            tk.Radiobutton(pf, text=provider_label(pid), variable=self.provider_var,
                           value=pid, command=self._on_provider_change, bg="#f5f5f5",
                           font=(FONT, 10), activebackground="#f5f5f5"
                           ).pack(side="left", padx=(0, 14))

        # —— Key ——
        kf = tk.Frame(r, bg="#f5f5f5")
        kf.pack(fill="x", padx=18, pady=4)
        tk.Label(kf, text="API Key", bg="#f5f5f5", width=9, anchor="w",
                 font=(FONT, 10)).pack(side="left")
        self.key_var = tk.StringVar()
        self.key_entry = tk.Entry(kf, textvariable=self.key_var, show="*", width=52,
                                  font=("Consolas", 10))
        self.key_entry.pack(side="left", ipady=2)
        self.show_var = tk.BooleanVar(value=False)
        tk.Checkbutton(kf, text="显示", variable=self.show_var,
                       command=self._toggle_key_visibility, bg="#f5f5f5",
                       font=(FONT, 9)).pack(side="left", padx=(8, 0))

        # —— 模型 ——
        mf = tk.Frame(r, bg="#f5f5f5")
        mf.pack(fill="x", padx=18, pady=4)
        tk.Label(mf, text="模型", bg="#f5f5f5", width=9, anchor="w",
                 font=(FONT, 10)).pack(side="left")
        self.model_var = tk.StringVar()
        tk.Entry(mf, textvariable=self.model_var, width=32,
                 font=("Consolas", 10)).pack(side="left", ipady=2)

        # —— 提示 ——
        self.tip_label = tk.Label(r, text="", bg="#f5f5f5", fg="#555555",
                                  font=(FONT, 9), justify="left", anchor="w",
                                  wraplength=590)
        self.tip_label.pack(fill="x", padx=18, pady=(10, 2))

        # —— 状态行 ——
        self.status_label = tk.Label(r, text="", bg="#f5f5f5", fg="#666666",
                                     font=(FONT, 9), anchor="w")
        self.status_label.pack(fill="x", padx=18, pady=(4, 0))

        # —— 按钮 ——
        bf = tk.Frame(r, bg="#f5f5f5")
        bf.pack(fill="x", padx=18, pady=(10, 14), side="bottom")
        self.test_btn = ttk.Button(bf, text="测试连接", command=self._test)
        self.test_btn.pack(side="left")
        ttk.Button(bf, text="保存并启动 ▶", command=self._save).pack(side="right")
        ttk.Button(bf, text="稍后配置", command=self._skip).pack(side="right", padx=8)

        self._on_provider_change()
        self.key_entry.focus_set()
        r.bind("<Return>", lambda e: self._save())

    def _center(self):
        self.root.update_idletasks()

    # ------------------------------------------------------------------ 交互
    def _on_provider_change(self):
        pid = self.provider_var.get()
        meta = PROVIDERS[pid]
        # 已有 Key 时回填（方便用户改模型/换 Key），否则空
        self.key_var.set(self.cfg.get(meta["key_field"], "") or "")
        self.model_var.set(self.cfg.get(meta["model_field"], "")
                           or DEFAULT_MODELS.get(pid, ""))
        self.tip_label.config(text=TIPS.get(pid, ""))
        self._set_status("")

    def _toggle_key_visibility(self):
        self.key_entry.config(show="" if self.show_var.get() else "*")

    def _set_status(self, text: str, ok: bool | None = None):
        color = "#666666" if ok is None else ("#1a7f37" if ok else "#c62828")
        self.status_label.config(text=text, fg=color)

    # ------------------------------------------------------------------ 动作
    def _test(self):
        pid = self.provider_var.get()
        key = self.key_var.get().strip()
        model = self.model_var.get().strip()
        if not key:
            self._set_status("请先填写 API Key", ok=False)
            return
        self._set_status("正在测试连接…")
        self.test_btn.config(state="disabled")

        def _worker():
            from ai_client import test_connection
            ok, msg = test_connection(pid, key, model)
            self.root.after(0, lambda: self._on_test_done(ok, msg))

        threading.Thread(target=_worker, daemon=True, name="setup-test").start()

    def _on_test_done(self, ok: bool, msg: str):
        self.test_btn.config(state="normal")
        self._set_status(("✅ " if ok else "❌ ") + msg, ok=ok)

    def _save(self):
        pid = self.provider_var.get()
        meta = PROVIDERS[pid]
        key = self.key_var.get().strip()
        model = self.model_var.get().strip() or DEFAULT_MODELS.get(pid, "")
        if not key:
            self._set_status("请先填写 API Key（或点「稍后配置」先跳过）", ok=False)
            return
        if not model:
            self._set_status("豆包需要填写接入点 ID（形如 ep-xxxxxxxx），不能为空", ok=False)
            return
        self.cfg[meta["key_field"]] = key
        self.cfg[meta["model_field"]] = model
        self.cfg["ai_provider"] = pid
        try:
            cfg_mod.save(self.cfg)
        except Exception as e:
            self._set_status(f"保存失败：{e}", ok=False)
            return
        log.info("首次配置完成：provider=%s model=%s", pid, model)
        self.result = self.cfg
        self.root.destroy()

    def _skip(self):
        log.info("用户跳过首次配置")
        self.result = None
        self.root.destroy()

    # ------------------------------------------------------------------ 入口
    def run(self) -> dict | None:
        self.root.mainloop()
        return self.result


def run_setup_wizard(cfg: dict) -> dict | None:
    """打开首次配置向导；返回保存后的 cfg（跳过/关闭返回 None）。"""
    try:
        return SetupWizard(cfg).run()
    except Exception as e:  # 向导异常不能挡住主程序
        log.exception("配置向导异常（已跳过）: %s", e)
        return None


if __name__ == "__main__":
    # 自测：python setup_wizard.py
    logging.basicConfig(level=logging.INFO)
    c = cfg_mod.load()
    new_cfg = run_setup_wizard(c)
    print("向导返回:", "已保存" if new_cfg else "跳过")
    if new_cfg:
        print("ai_provider =", new_cfg.get("ai_provider"))
        print("Key 长度:", len(new_cfg.get(PROVIDERS[new_cfg["ai_provider"]]["key_field"], "")))
