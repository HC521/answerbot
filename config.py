# -*- coding: utf-8 -*-
"""配置模块：config.json 的读取与写入。

- 加载时缺失字段用默认值补齐（保证老配置/手写配置也能直接跑）；
- 所有 API Key 集中在本文件管理的 config.json 中，代码内不写死密钥；
- 悬浮窗位置等运行期变更会回写 config.json，实现"重启记忆"。
"""

import json
import os
import sys
import threading


def _base_dir() -> str:
    """程序所在目录：源码运行=代码目录；打包 exe=exe 所在目录（onefile 下 __file__ 是临时目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


# 默认配置：字段缺失时以此补齐
DEFAULT_CONFIG = {
    # —— 抓屏（v2.1 变更 A：全屏抓取）——
    # 废弃字段保留读取但不再使用（兼容旧配置）：vm_window_keyword / capture_mode / mss_monitor
    "capture_monitor": 0,               # 全屏抓取：0=所有显示器合并（默认），1..N=指定显示器
    "roi": None,                        # 归一化裁剪 [l,t,r,b]（0~1），null=全屏（一般用自动 ROI）

    # —— AI ——
    "ark_api_key": "",                  # 豆包（火山方舟）API Key
    "ark_model": "",                    # 豆包模型/接入点 ID（ep-xxx），以火山方舟控制台为准
    "dashscope_api_key": "",            # 通义千问（DashScope）API Key
    "qwen_model": "qwen-vl-plus",       # 千问模型
    "ai_provider": "doubao",            # doubao / qwen

    # —— 变化检测 ——
    "detect_interval_ms": 800,          # 取帧间隔（毫秒）
    "t_change": 12,                     # 变化判定阈值（汉明距离）
    "t_stable": 4,                      # 稳定判定阈值
    "n_stable": 6,                      # 连续稳定帧数
    "t_timeout_ms": 30000,              # 变化后最大等待稳定时间
    "diff_threshold": 25,               # 像素差分阈值（v2.1 变更 B）
    "diff_area_ratio": 0.005,           # 杂散小区域过滤：差异像素占比阈值（v2.1）
    "question_roi": None,               # 题目区域 [x,y,w,h]（自动学习并持久化，可手动改）

    # —— 定时兜底识别（FR-12，v2.1 变更 C）——
    "fallback_interval_ms": 30000,      # 距上次识别超过此毫秒数 → 强制识别一次（0=关闭）
    "dedupe_by_answer": True,           # 结果去重：true=答案+题目摘要都相同才去重；false=仅答案

    # —— 悬浮窗 ——
    "overlay_pos": [None, None],        # 悬浮窗位置 [x, y]，null=默认右上角
    "overlay_alpha": 0.85,              # 透明度
    "overlay_rect": None,               # 悬浮窗像素矩形 [x,y,w,h]（合并图坐标，自动换算，可覆盖）
    "hotkey": "ctrl+alt+h",             # 全局隐藏/显示热键

    # —— 手动触发（FR-11，v2.1 变更 F：虚拟机内侧键可能不生效，仅辅助）——
    "manual_trigger": "",               # 手动识别触发键（如 "mouse:x1"），空=关闭
    "manual_cooldown_ms": 5000,         # 手动触发冷却

    # —— 其它 ——
    "copy_to_clipboard": False,         # 答案自动复制到剪贴板
    "question_counter": 0,              # 题号计数（自动编号，FR-10）
}

_LOCK = threading.Lock()
_cfg = None


def _config_path() -> str:
    """config.json 路径：与程序（exe）同目录。"""
    return os.path.join(_base_dir(), "config.json")


def load() -> dict:
    """加载配置；文件不存在时用默认值创建；缺失字段用默认值补齐。"""
    global _cfg
    with _LOCK:
        cfg = dict(DEFAULT_CONFIG)
        path = _config_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f)
                if isinstance(user_cfg, dict):
                    cfg.update(user_cfg)
            except Exception:
                # 配置损坏时保留默认值，不阻塞启动；由 main 层记录日志
                pass
        else:
            save(cfg)  # 首次运行自动生成默认配置
        _cfg = cfg
        return cfg


def get(key: str, default=None):
    """读取单个配置项（惰性加载）。"""
    global _cfg
    if _cfg is None:
        load()
    return _cfg.get(key, default)


def set(key: str, value) -> None:
    """修改单个配置项并立即落盘（如悬浮窗位置、绑定按键、题号）。"""
    global _cfg
    with _LOCK:
        if _cfg is None:
            load()
        _cfg[key] = value
        save(_cfg)


def save(cfg: dict) -> None:
    """把配置写回 config.json（UTF-8，带缩进，人工可读可改）。"""
    path = _config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # 自测：python config.py
    c = load()
    print("config.json 路径:", _config_path())
    print("配置项数:", len(c))
    print("vm_window_keyword =", c["vm_window_keyword"])
    assert c["ark_api_key"] == "" and c["dashscope_api_key"] == ""
    print("OK：config 读写正常")
