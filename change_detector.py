# -*- coding: utf-8 -*-
"""变化检测模块（FR-03 / FR-04，v2.1 变更 B）。

- 均值 pHash（64bit）：缩略图 → 32×32 灰度 → 3×3 均值模糊（抗抖动）→ 与均值比较；
- 状态机：IDLE --(d≥t_change)--> CHANGING --(连续 n_stable 帧 d≤t_stable)--> STABLE；
- 超时兜底：CHANGING 超过 t_timeout_ms 未稳定 → 放弃回 IDLE；
- 基准帧抑制：识别完成后记录基准帧，与基准帧差异小则抑制重复触发（防同一题反复识别）；
- 题目区域定位（v2.1）：CHANGING 期间对相邻帧做像素差分，收集差异矩形，
  稳定后取并集外接框作为 question_roi（小面积杂散区域按占比过滤；
  与上一题 ROI 重叠时沿用旧 ROI，布局稳定时更快更准）；
- exclude_rects：悬浮窗等区域在差分时排除（避免自身变化干扰）。
"""

import time

import numpy as np
from PIL import Image

log = None  # loop 层负责日志


def phash(img: Image.Image, size: int = 32) -> int:
    """均值 pHash：返回 64bit 整数指纹。"""
    g = np.asarray(img.convert("L").resize((size, size)), dtype=np.float32)
    # 3×3 均值模糊抗抖动（edge padding 后滑动窗口求均值）
    g = np.pad(g, 1, mode="edge")
    blurred = np.zeros((size, size), dtype=np.float32)
    for i in range(size):
        for j in range(size):
            blurred[i, j] = g[i : i + 3, j : j + 3].mean()
    mean = blurred.mean()
    bits = (blurred > mean).flatten()
    return sum(int(b) << i for i, b in enumerate(bits))


def hamming(a: int, b: int) -> int:
    """两个 pHash 的汉明距离（差异位数）。"""
    return bin(a ^ b).count("1")


def diff_region(prev: np.ndarray, cur: np.ndarray,
                exclude_rects=None, threshold: int = 25,
                area_ratio: float = 0.005) -> tuple | None:
    """相邻帧像素差分，返回差异区域外接矩形 (x, y, w, h)。

    - threshold: 单像素差异阈值；
    - exclude_rects: 排除区域列表（悬浮窗等），[(x, y, w, h), ...]；
    - area_ratio: 差异像素占比低于该值视为杂散小区域（倒计时跳动等），返回 None。
    """
    d = np.abs(cur.astype(np.int16) - prev.astype(np.int16)).sum(axis=2)
    mask = d > threshold
    if exclude_rects:
        for (ex, ey, ew, eh) in exclude_rects:
            sx0 = max(0, int(ex))
            sy0 = max(0, int(ey))
            mask[sy0 : sy0 + int(eh), sx0 : sx0 + int(ew)] = False
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    total = mask.size
    if len(xs) < total * area_ratio:
        return None  # 杂散小区域，忽略
    return (int(xs.min()), int(ys.min()),
            int(xs.max() - xs.min()), int(ys.max() - ys.min()))


def _rects_union(rects: list[tuple]) -> tuple | None:
    """多个矩形 (x,y,w,h) 的并集外接框；空列表返回 None。"""
    if not rects:
        return None
    xs0 = min(r[0] for r in rects)
    ys0 = min(r[1] for r in rects)
    xs1 = max(r[0] + r[2] for r in rects)
    ys1 = max(r[1] + r[3] for r in rects)
    return (xs0, ys0, xs1 - xs0, ys1 - ys0)


def _rects_overlap(a: tuple, b: tuple) -> bool:
    """两个矩形 (x,y,w,h) 是否有重叠。"""
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    return not (ax0 + aw <= bx0 or bx0 + bw <= ax0
                or ay0 + ah <= by0 or by0 + bh <= ay0)


class ChangeDetector:
    """画面变化检测状态机。每次喂一帧缩略图，返回事件字符串。"""

    # 事件常量
    IDLE = "idle"          # 无变化
    CHANGING = "changing"  # 检测到变化，等待稳定
    STABLE = "stable"      # 变化后已稳定（新题完整显示）
    TIMEOUT = "timeout"    # 变化后超时未稳定（放弃本次）
    SUPPRESSED = "suppressed"  # 与基准帧差异小，抑制重复触发

    def __init__(
        self,
        t_change: int = 12,
        t_stable: int = 4,
        n_stable: int = 6,
        t_timeout_ms: int = 30000,
        diff_threshold: int = 25,
        diff_area_ratio: float = 0.005,
        question_roi: tuple | None = None,
    ):
        self.t_change = t_change
        self.t_stable = t_stable
        self.n_stable = n_stable
        self.t_timeout_s = t_timeout_ms / 1000.0
        self.diff_threshold = diff_threshold
        self.diff_area_ratio = diff_area_ratio

        self.state = self.IDLE
        self.prev_img: Image.Image | None = None   # 上一帧缩略图
        self.prev_hash: int | None = None          # 上一帧指纹
        self.base_hash: int | None = None          # 识别完成后的基准帧指纹
        self.stable_count = 0
        self.changing_since = 0.0
        self._diff_rects: list[tuple] = []         # 本次变化期间的差异矩形
        self.question_roi: tuple | None = question_roi  # 题目区域（上一题）

    def feed(self, img: Image.Image, exclude_rects=None) -> str:
        """输入一帧缩略图，推进状态机，返回事件。"""
        h = phash(img)

        if self.prev_hash is None:
            self.prev_img = img
            self.prev_hash = h
            return self.IDLE

        d = hamming(h, self.prev_hash)
        self.prev_hash = h  # 相邻帧比较：每帧都更新
        now = time.monotonic()

        # —— 基准帧抑制：识别完成后画面未变 → 不重复触发 ——
        if (
            self.state == self.IDLE
            and self.base_hash is not None
            and hamming(h, self.base_hash) <= self.t_stable
        ):
            self.prev_img = img  # 差分基准保持最新帧
            return self.SUPPRESSED

        if self.state == self.IDLE:
            if d >= self.t_change:
                self.state = self.CHANGING
                self.stable_count = 0
                self.changing_since = now
                self._diff_rects = []
                self._collect_diff(img, exclude_rects)
                return self.CHANGING
            self.prev_img = img
            return self.IDLE

        # —— CHANGING ——
        if now - self.changing_since > self.t_timeout_s:
            self.state = self.IDLE
            self.stable_count = 0
            self._diff_rects = []
            self.prev_img = img  # 差分基准保持最新帧
            return self.TIMEOUT

        if d <= self.t_stable:
            self.stable_count += 1
            if self.stable_count >= self.n_stable:
                self.state = self.IDLE
                self.stable_count = 0
                self._finalize_roi()
                return self.STABLE
        else:
            self.stable_count = 0
            self._collect_diff(img, exclude_rects)
        self.prev_img = img
        return self.CHANGING

    def _collect_diff(self, img: Image.Image, exclude_rects) -> None:
        """记录与上一帧的差异矩形（面积过滤后），用于 ROI 收敛。"""
        if self.prev_img is None:
            return
        prev = np.asarray(self.prev_img.convert("RGB"), dtype=np.uint8)
        cur = np.asarray(img.convert("RGB"), dtype=np.uint8)
        if prev.shape != cur.shape:
            return
        r = diff_region(prev, cur, exclude_rects,
                        threshold=self.diff_threshold,
                        area_ratio=self.diff_area_ratio)
        if r is not None:
            self._diff_rects.append(r)

    def _finalize_roi(self) -> None:
        """稳定后收敛题目区域：并集外接框；与上一题 ROI 重叠则沿用旧 ROI。"""
        new_roi = _rects_union(self._diff_rects)
        self._diff_rects = []
        if new_roi is None:
            return
        if self.question_roi and _rects_overlap(new_roi, self.question_roi):
            return  # 布局稳定：沿用上一题 ROI（更快更准）
        self.question_roi = new_roi

    def set_base(self, img: Image.Image) -> None:
        """识别完成后记录基准帧，抑制同一题重复触发。"""
        self.base_hash = phash(img)

    def reset(self) -> None:
        """清空全部状态（手动重新开始时使用）。"""
        self.prev_img = None
        self.prev_hash = None
        self.base_hash = None
        self.state = self.IDLE
        self.stable_count = 0
        self._diff_rects = []


if __name__ == "__main__":
    # 自测：python change_detector.py
    from PIL import Image, ImageDraw

    def make_img(bg: int, text: str, x: int = 20, text_color: int = 255) -> Image.Image:
        img = Image.new("L", (200, 100), bg)
        d = ImageDraw.Draw(img)
        d.text((x, 40), text, fill=text_color)
        return img

    a = make_img(30, "hello")
    b = make_img(30, "world", x=130)  # 内容+位置都变（模拟翻页），差异足够大
    c = make_img(30, "hello")         # 回到 a
    same = make_img(30, "hello")

    ha, hb, hc, hs = phash(a), phash(b), phash(c), phash(same)
    print("a vs b 距离:", hamming(ha, hb))
    print("a vs c 距离:", hamming(ha, hc))
    print("a vs same 距离:", hamming(ha, hs))
    assert hamming(ha, hb) > 0 and hamming(ha, hs) == 0

    det = ChangeDetector(t_change=4, t_stable=0, n_stable=3, t_timeout_ms=10000)
    events = [det.feed(a)]            # idle（首帧）
    for _ in range(3):
        events.append(det.feed(b))    # 变化 → changing ×3
    assert events == ["idle", "changing", "changing", "changing"], events
    events.clear()
    for _ in range(5):
        events.append(det.feed(same))  # 稳定
    print("稳定阶段事件:", events)
    assert det.STABLE in events
    assert events[0] == det.CHANGING or events[0] == det.SUPPRESSED

    # 基准帧抑制
    det.set_base(same)
    assert det.feed(same) == det.SUPPRESSED
    assert det.feed(c) == det.SUPPRESSED
    print("基准帧抑制 OK")
    print("change_detector 自测通过")
