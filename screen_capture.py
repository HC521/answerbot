# -*- coding: utf-8 -*-
"""全屏抓取模块（v2.1 变更 A，解决 P1/P2）。

- 不再依赖窗口标题定位（VMware / 天翼云电脑等载体通用）；
- mss 全屏抓取：capture_monitor=0 合并全部显示器（默认），1..N 指定显示器；
- 黑屏/异常检测保留，失败抛 ScreenCaptureError 由主循环重试，不崩溃；
- 检测链路用缩略图（宽 ≤1280），识别链路用原图 + ROI 裁剪。
"""

import logging

from PIL import Image

log = logging.getLogger("screen_capture")

_origin_cache: tuple[int, int] | None = None


class ScreenCaptureError(Exception):
    """抓屏失败（全屏抓取异常/黑屏）。"""


def capture_fullscreen(monitor: int = 0) -> Image.Image:
    """mss 全屏抓取。

    monitor: 0 = 所有显示器合并（默认，题目可能在任一屏）；
             1..N = 指定显示器编号。
    返回 RGB 图；异常抛 ScreenCaptureError。
    """
    import mss

    try:
        with mss.mss() as sct:
            monitors = sct.monitors
            idx = monitor if 0 <= monitor < len(monitors) else 0
            shot = sct.grab(monitors[idx])
            return Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception as e:
        log.warning("全屏抓取异常: %s", e)
        raise ScreenCaptureError(f"全屏抓取失败: {e}") from e


def monitor_origin() -> tuple[int, int]:
    """合并图坐标系原点（sct.monitors[0] 的 left/top），供悬浮窗矩形换算。

    多屏时 monitors[0] 的 left/top 可能为负（副屏在主屏左侧），
    悬浮窗物理坐标 - origin = 合并图坐标。结果缓存（显示器布局变化需重启）。
    """
    global _origin_cache
    if _origin_cache is None:
        import mss

        with mss.mss() as sct:
            m = sct.monitors[0]
            _origin_cache = (m["left"], m["top"])
    return _origin_cache


def is_black(img: Image.Image, variance_threshold: float = 5.0) -> bool:
    """黑屏检测：灰度方差极小（近乎纯色）视为抓取失败/黑屏。"""
    import numpy as np

    gray = np.asarray(img.convert("L"), dtype=np.float32)
    if gray.size == 0:
        return True
    return float(gray.var()) < variance_threshold


def apply_roi(img: Image.Image, roi) -> Image.Image:
    """归一化 ROI 裁剪 [l, t, r, b]（0~1）；roi 为 None 时返回原图。"""
    if not roi:
        return img
    w, h = img.size
    l, t, r, b = roi
    box = (int(l * w), int(t * h), int(r * w), int(b * h))
    return crop_pixels(img, box)


def crop_pixels(img: Image.Image, rect) -> Image.Image:
    """像素矩形裁剪 (x, y, w, h)；防御越界；rect 为 None 返回原图。"""
    if not rect:
        return img
    x, y, w, h = (int(v) for v in rect)
    iw, ih = img.size
    x0 = max(0, min(x, iw - 1))
    y0 = max(0, min(y, ih - 1))
    x1 = max(x0 + 1, min(x + w, iw))
    y1 = max(y0 + 1, min(y + h, ih))
    return img.crop((x0, y0, x1, y1))


def mask_rect(img: Image.Image, rect, fill=(255, 255, 255)) -> Image.Image:
    """把矩形区域填充为 fill（v2.3：全图识别前排除悬浮窗区域，防模型读到悬浮窗）。"""
    if not rect:
        return img
    import numpy as np

    arr = np.asarray(img.convert("RGB")).copy()
    x, y, w, h = (int(v) for v in rect)
    x0, y0 = max(0, x), max(0, y)
    x1 = min(img.width, x + w)
    y1 = min(img.height, y + h)
    if x1 > x0 and y1 > y0:
        arr[y0:y1, x0:x1] = fill
    return Image.fromarray(arr)


class ScreenCapture:
    """按 config 抓全屏；提供缩略图供检测链路使用。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.monitor = cfg.get("capture_monitor", 0)

    def capture(self) -> Image.Image:
        """抓一帧全屏原图；黑屏/失败抛 ScreenCaptureError。"""
        img = capture_fullscreen(self.monitor)
        if is_black(img):
            log.warning("全屏抓取黑屏（客户端独占全屏时 mss 抓不到），重试")
            raise ScreenCaptureError(
                "全屏黑屏：若云电脑/虚拟机客户端为独占全屏模式，"
                "请改为「窗口/无边框全屏」模式运行")
        return img

    def thumbnail(self, img: Image.Image, max_width: int = 1280) -> Image.Image:
        """全屏图 → 检测用缩略图（宽 ≤ max_width，等比例）。"""
        w, h = img.size
        if w <= max_width:
            return img
        nw = max_width
        nh = max(1, int(h * max_width / w))
        return img.resize((nw, nh), Image.LANCZOS)


if __name__ == "__main__":
    # 自测：python screen_capture.py
    sc = ScreenCapture({"capture_monitor": 0})
    img = sc.capture()
    print("全屏抓取:", img.size, img.mode, "黑屏:", is_black(img))
    thumb = sc.thumbnail(img)
    print("缩略图:", thumb.size)
    print("合并图原点:", monitor_origin())
    img.save("_selftest_capture.png")
    print("已保存 _selftest_capture.png")
