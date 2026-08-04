# -*- coding: utf-8 -*-
"""手动触发与按键录制模块（FR-11）。

- 运行态：全局监听已绑定键（鼠标侧键 `mouse:x1` 或键盘组合 `ctrl+alt+f9`），
  按下即触发手动识别（跳过变化检测，走 截图→AI→显示 同一流程）；
- 录制态：悬浮窗「绑定按键」→ 按一下想用的键即绑定；
  拒绝鼠标左键/滚轮（防答题误触发）；Esc 取消；可随时重绑；
- 未绑定（config `manual_trigger` 为空）时功能整体关闭，不启动监听、不占资源；
- 触发后有冷却（config `manual_cooldown_ms`，默认 5s）防误触/连点。
"""

import logging
import threading
import time

import config as cfg_mod

log = logging.getLogger("input_listener")

# 录制时拒绝的按键（鼠标左键/中键滚轮）
_REJECT_BUTTONS = {"left", "middle", "scroll"}
# 修饰键规范化名（keyboard 库格式）
_MODIFIER_NAMES = {"ctrl": "ctrl", "alt": "alt", "shift": "shift", "cmd": "win"}
# 模块级强引用持有（v2.1 变更 F，P3）：pynput Listener 若失去引用会被 GC 回收，
# 回调静默失效（表现为"绑定了但按了没反应"）；实例属性 + 模块级双保险。
_listener_hold: list = []


class InputListener:
    """全局按键监听：运行触发 + 录制绑定。trigger_cb 由 loop 注入（手动识别入口）。"""

    def __init__(self, cfg: dict, trigger_cb):
        self.cfg = cfg
        self.trigger_cb = trigger_cb
        self.recording = False
        self._record_cb = None           # 录制完成回调（参数：绑定字符串或 None=取消）
        self._mouse = None
        self._kb = None
        self._pressed_mods = set()       # 当前按下的修饰键（运行/录制共用）
        self._last_trigger = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self):
        """启动运行态监听；未绑定时静默关闭（不占资源）。"""
        if not self.cfg.get("manual_trigger"):
            log.info("手动触发未绑定，功能关闭")
            return
        self._start_listeners()

    def stop(self):
        self._stop_listeners()

    def _start_listeners(self):
        """启动运行态监听；未绑定时静默关闭（不占资源）。"""
        if not self.cfg.get("manual_trigger"):
            log.info("手动触发未绑定，功能关闭")
            return
        from pynput import keyboard, mouse

        self._mouse = mouse.Listener(on_click=self._on_mouse_click)
        self._kb = keyboard.Listener(
            on_press=self._on_kb_press, on_release=self._on_kb_release)
        self._mouse.daemon = True
        self._kb.daemon = True
        self._mouse.start()
        self._kb.start()
        _listener_hold.append(self._mouse)   # 模块级持有，防 GC 回收（P3）
        _listener_hold.append(self._kb)
        log.info("手动触发监听已启动: %s", self.cfg.get("manual_trigger"))

    def _stop_listeners(self):
        for l in (self._mouse, self._kb):
            try:
                if l and l.running:
                    l.stop()
            except Exception:
                pass
        try:
            for l in (self._mouse, self._kb):
                if l in _listener_hold:
                    _listener_hold.remove(l)
        except Exception:
            pass
        self._mouse = self._kb = None

    # ------------------------------------------------------------------
    # 录制（FR-11 交互流程）
    # ------------------------------------------------------------------
    def record(self, on_done):
        """进入录制状态：停运行监听，等待用户按一个合法按键，成功后保存并重启监听。"""
        if self.recording:
            return
        self.recording = True
        self._record_cb = on_done
        self._stop_listeners()
        from pynput import keyboard, mouse
        self._mouse = mouse.Listener(on_click=self._on_mouse_click)
        self._kb = keyboard.Listener(
            on_press=self._on_kb_press, on_release=self._on_kb_release)
        self._mouse.daemon = True
        self._kb.daemon = True
        self._mouse.start()
        self._kb.start()
        log.info("进入按键录制状态")

    def _finish_record(self, binding: str | None):
        """binding=None 表示取消（Esc）。取消后恢复原绑定监听。"""
        self.recording = False
        self._stop_listeners()
        if binding:
            cfg_mod.set("manual_trigger", binding)
            log.info("已绑定触发键: %s", binding)
        elif self.cfg.get("manual_trigger"):
            log.info("取消录制，保留原绑定: %s", self.cfg.get("manual_trigger"))
        # 无论绑定/取消都恢复运行监听（未绑定时 _start_listeners 静默跳过）
        self._start_listeners()
        if self._record_cb:
            cb, self._record_cb = self._record_cb, None
            cb(binding)

    # ------------------------------------------------------------------
    # 鼠标事件
    # ------------------------------------------------------------------
    def _on_mouse_click(self, x, y, button, pressed):
        if not pressed:
            return
        name = button.name

        if self.recording:
            if name in _REJECT_BUTTONS:
                return  # 拒绝左键/滚轮，防答题误触发
            self._finish_record(f"mouse:{name}")
            return

        bound = self.cfg.get("manual_trigger")
        if bound == f"mouse:{name}":
            self._trigger()

    # ------------------------------------------------------------------
    # 键盘事件
    # ------------------------------------------------------------------
    def _on_kb_press(self, key):
        from pynput.keyboard import Key, KeyCode

        # 维护修饰键集合
        if isinstance(key, Key):
            if key in (Key.ctrl_l, Key.ctrl_r):
                self._pressed_mods.add("ctrl")
            elif key in (Key.alt_l, Key.alt_r, Key.alt_gr):
                self._pressed_mods.add("alt")
            elif key in (Key.shift_l, Key.shift_r):
                self._pressed_mods.add("shift")
            elif key in (Key.cmd, Key.cmd_r):
                self._pressed_mods.add("win")
        elif isinstance(key, KeyCode):
            if key.char and key.char.lower() in ("ctrl", "alt", "shift", "win"):
                # 极少平台下修饰键以 KeyCode 上报，规范化
                self._pressed_mods.add(key.char.lower())

        if self.recording:
            self._handle_record_kb(key)
            return

        bound = self.cfg.get("manual_trigger")
        if bound and self._kb_matches(bound, key):
            self._trigger()

    def _on_kb_release(self, key):
        from pynput.keyboard import Key, KeyCode

        if isinstance(key, Key):
            if key in (Key.ctrl_l, Key.ctrl_r):
                self._pressed_mods.discard("ctrl")
            elif key in (Key.alt_l, Key.alt_r, Key.alt_gr):
                self._pressed_mods.discard("alt")
            elif key in (Key.shift_l, Key.shift_r):
                self._pressed_mods.discard("shift")
            elif key in (Key.cmd, Key.cmd_r):
                self._pressed_mods.discard("win")
        elif isinstance(key, KeyCode) and key.char:
            mod = key.char.lower()
            if mod in _MODIFIER_NAMES:
                self._pressed_mods.discard(mod)

    def _handle_record_kb(self, key):
        """录制态键盘处理：Esc 取消；非修饰键按下即绑定（含组合）。"""
        from pynput.keyboard import Key, KeyCode

        if key == Key.esc:
            self._finish_record(None)
            return
        if isinstance(key, KeyCode) and key.char and key.char.lower() in _MODIFIER_NAMES:
            return  # 只按修饰键不算，等待主键
        if isinstance(key, Key):
            name = key.name  # f1~f24 / esc / space / enter …
        else:
            name = key.char or key.vk
        if not name:
            return
        parts = list(self._pressed_mods) + [str(name)]
        self._finish_record("+".join(parts))

    def _kb_matches(self, bound: str, key) -> bool:
        """判断当前按下的 key 是否命中键盘格式绑定（如 ctrl+alt+f9）。"""
        from pynput.keyboard import Key, KeyCode

        if "mouse:" in bound:
            return False
        parts = bound.split("+")
        mods, main = set(parts[:-1]), parts[-1]

        if isinstance(key, Key):
            cur = key.name
        elif isinstance(key, KeyCode):
            cur = key.char or str(key.vk)
        else:
            return False

        if str(cur).lower() != main.lower():
            return False
        return mods == self._pressed_mods

    # ------------------------------------------------------------------
    # 触发
    # ------------------------------------------------------------------
    def _trigger(self):
        """带冷却的手动触发入口。"""
        cooldown = self.cfg.get("manual_cooldown_ms", 5000) / 1000.0
        now = time.monotonic()
        with self._lock:
            if now - self._last_trigger < cooldown:
                log.info("手动触发冷却中，忽略")
                return
            self._last_trigger = now
        log.info("手动触发按键按下")
        self.trigger_cb()


if __name__ == "__main__":
    # 自测：python input_listener.py  （3 秒后自动录制一个键并显示）
    import time

    c = cfg_mod.load()
    c["manual_trigger"] = ""  # 确保从录制开始
    il = InputListener(c, lambda: print(">>> 手动识别被触发"))

    def demo():
        time.sleep(1)
        print("3 秒内请按一个键（Esc 取消）…")
        il.record(lambda b: print("录制结果:", b))
        time.sleep(6)
        il.stop()
        print("自测结束")

    threading.Thread(target=demo, daemon=True).start()
    time.sleep(1)
    print("监听器就绪")
    # 演示模式：不阻塞（正常使用由 main 启动）
    il.start()
    time.sleep(8)
