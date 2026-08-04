# -*- coding: utf-8 -*-
"""主循环状态机（FR-03/04/05/10 + FR-11 手动触发 + FR-12 定时兜底，v2.1）。

```
IDLE --(d≥t_change)--> CHANGING --(连续 n_stable 帧稳定)--> 截图ROI+AI --> 显示 --> IDLE
  ▲                                                                           │
  └──────────────(超时 / 识别失败 / 显示完成，均回到 IDLE)──────────────────────┘
```

- 独立线程运行，GUI 主线程不阻塞；
- 全屏抓取（v2.1）：不依赖窗口标题，载体通用；
- 题目区域 ROI：变化期间差分定位，识别只发 ROI 裁剪图（悬浮窗区域自动排除）；
- 定时兜底（FR-12，v2.1）：距上次识别超过 fallback_interval_ms 强制识别一次
  （饱和式兜底，解决变化检测卡住/侧键失效场景）；结果去重避免重复显示；
- 识别失败：悬浮窗显示「第 N 题 识别失败（原因）」，不阻塞下一题检测；
- 豆包失败自动尝试千问（备）；手动触发（FR-11）与自动流程互斥。
"""

import logging
import threading
import time

import config as cfg_mod
from ai_client import AiError, ask_ai
from change_detector import ChangeDetector
from screen_capture import ScreenCapture, ScreenCaptureError, crop_pixels

log = logging.getLogger("loop")


class AnswerLoop:
    """答题主循环。run() 在独立线程执行；overlay 回调由 main 注入。"""

    def __init__(self, cfg: dict, overlay):
        self.cfg = cfg
        self.overlay = overlay
        self.capture = ScreenCapture(cfg)
        self.detector = ChangeDetector(
            t_change=cfg.get("t_change", 12),
            t_stable=cfg.get("t_stable", 4),
            n_stable=cfg.get("n_stable", 6),
            t_timeout_ms=cfg.get("t_timeout_ms", 30000),
            diff_threshold=cfg.get("diff_threshold", 25),
            diff_area_ratio=cfg.get("diff_area_ratio", 0.005),
            question_roi=cfg.get("question_roi"),
        )
        self.running = True
        self.paused = False
        self._lock = threading.Lock()          # 手动/自动识别互斥
        self._busy = False                     # 是否正在识别（防重入）
        self._last_recognize = 0.0             # 上次识别结束时间（兜底计时）
        self._last_result = None               # (answer, summary) 去重用

    # ------------------------------------------------------------------
    # 对外控制
    # ------------------------------------------------------------------
    def toggle_pause(self):
        self.paused = not self.paused
        log.info("循环%s", "已暂停" if self.paused else "已恢复")
        return self.paused

    def stop(self):
        self.running = False

    def manual_trigger(self):
        """手动识别（FR-11）：跳过变化检测，直接 截图→AI→显示。"""
        if not self.running or self.paused:
            return
        try:
            img = self.capture.capture()
            thumb = self.capture.thumbnail(img)
        except ScreenCaptureError as e:
            log.warning("手动触发抓屏失败: %s", e)
            self.overlay.set_status("抓屏失败，稍后再试")
            return
        self._recognize(img, thumb, manual=True)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run(self):
        """主循环线程体。"""
        log.info("主循环启动")
        interval = self.cfg.get("detect_interval_ms", 800) / 1000.0
        fallback_s = self.cfg.get("fallback_interval_ms", 30000) / 1000.0
        while self.running:
            try:
                if self.paused:
                    time.sleep(0.2)
                    continue

                img = self.capture.capture()          # 全屏原图
                thumb = self.capture.thumbnail(img)   # 检测用缩略图

                event = self.detector.feed(thumb, exclude_rects=self._exclude_rects(thumb, img))
                if event == self.detector.STABLE:
                    log.info("检测到稳定变化，开始识别（ROI=%s）", self.detector.question_roi)
                    self._recognize(img, thumb)
                elif event == self.detector.TIMEOUT:
                    log.info("变化超时未稳定，放弃本次")
                    self.overlay.set_status("变化超时，已跳过")

                # —— FR-12 定时兜底：距上次识别超时且不忙 → 强制识别一次 ——
                now = time.monotonic()
                if fallback_s > 0 and now - self._last_recognize >= fallback_s:
                    log.info("定时兜底识别触发（距上次 %.0fs）", now - self._last_recognize)
                    self._recognize(img, thumb)
            except ScreenCaptureError as e:
                # 抓屏失败：提示并重试，不崩溃
                log.warning("抓屏失败: %s", e)
                self.overlay.set_status("抓屏失败，重试中…")
                time.sleep(2.0)
            except Exception as e:
                # 兜底：任何意外异常都记录并继续，绝不静默死线程
                log.exception("主循环异常（已忽略，继续运行）: %s", e)
                time.sleep(1.0)

            time.sleep(interval)

    def _exclude_rects(self, thumb, img):
        """悬浮窗区域 → 缩略图坐标系（差分排除用）；悬浮窗隐藏时返回 None。"""
        rect = self.overlay.get_rect_pixels()
        if not rect:
            return None
        sx = thumb.width / max(1, img.width)
        sy = thumb.height / max(1, img.height)
        x, y, w, h = rect
        return [(int(x * sx), int(y * sy), max(1, int(w * sx)), max(1, int(h * sy)))]

    # ------------------------------------------------------------------
    # 识别（自动/手动/兜底共用）
    # ------------------------------------------------------------------
    def _recognize(self, img, thumb, manual: bool = False):
        with self._lock:
            if self._busy:
                log.info("识别进行中，忽略本次%s触发", "手动" if manual else "自动")
                return
            self._busy = True
        try:
            self.overlay.set_status("识别中…")
            # 题目 ROI 是缩略图坐标（1280 宽基准），裁剪原图前换算回原图坐标
            roi = self.detector.question_roi or self.cfg.get("question_roi")
            crop = img
            if roi:
                sx = img.width / max(1, thumb.width)
                sy = img.height / max(1, thumb.height)
                x, y, w, h = roi
                crop = crop_pixels(img, (int(x * sx), int(y * sy),
                                         int(w * sx), int(h * sy)))
            result = self._ask_with_fallback(crop)
            if self._dedupe(result):
                log.info("与上次结果相同，去重（不重复显示）")
                self.overlay.set_status("同题重复识别，已去重")
                return
            self._show_result(result)
            # 持久化题目 ROI（下一题优先沿用）
            if self.detector.question_roi:
                cfg_mod.set("question_roi", list(self.detector.question_roi))
        except AiError as e:
            log.error("识别失败: %s", e)
            # 失败不清空已显示的答案：保留上次成功结果，状态行提示原因
            # （用户要求：兜底/手动触发失败时，屏幕上仍保留当前题目答案）
            self.overlay.set_status(f"识别失败：{e}")
        except Exception as e:  # 兜底：任何异常都不崩溃
            log.exception("识别过程异常")
            self.overlay.set_status(f"异常：{e}")
        finally:
            # 无论成败都记基准帧：画面是同一题，避免反复识别
            try:
                self.detector.set_base(img)
            except Exception:
                pass
            self._last_recognize = time.monotonic()
            self._busy = False

    def _dedupe(self, result: dict) -> bool:
        """结果去重：与上次显示结果比较（答案 + 题目摘要；可配置仅比答案）。
        答案为空的结果不参与去重（避免"失败显示"被当成结果）。"""
        if self._last_result is None:
            return False
        answer = (result.get("answer") or "").strip()
        if not answer:
            return False
        summary = (result.get("question") or "").replace("\n", " ").strip()
        if self.cfg.get("dedupe_by_answer", True):
            return (answer, summary) == self._last_result
        return answer == self._last_result[0]

    def _ask_with_fallback(self, img) -> dict:
        """豆包主 / 千问备；主失败且备已配置时自动切换。"""
        try:
            return ask_ai(self.cfg, img, provider="doubao")
        except AiError as e:
            if self.cfg.get("dashscope_api_key"):
                log.warning("豆包失败(%s)，切换千问", e)
                return ask_ai(self.cfg, img, provider="qwen")
            raise

    def _show_result(self, result: dict):
        """显示结果并自增题号（FR-10）。"""
        no = self.cfg.get("question_counter", 0) + 1
        cfg_mod.set("question_counter", no)

        answer = result.get("answer") or ""
        q = result.get("question") or ""
        # 摘要：question 截断（悬浮窗内部再截 120 字）
        summary = q.replace("\n", " ").strip()
        status = f"识别完成（第 {no} 题）"
        conf = result.get("confidence")
        if conf:
            status += f" 把握度 {float(conf):.0%}"
        log.info("第 %d 题：answer=%s summary=%s", no, answer, summary[:60])
        self._last_result = (answer.strip(), summary)
        self.overlay.show(no, answer, summary, status=status)
