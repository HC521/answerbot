# AnswerBot-Screen — AI 自动答题机器人（读屏版 v2.2）

个人自用的 Windows 桌面读屏答题辅助程序：**全屏抓取**（不依赖特定客户端窗口），
自动检测「下一题出现并稳定显示」→ 视觉大模型（豆包主 / 千问备）识别 →
答案显示在**屏幕 1** 的置顶半透明悬浮窗，抬眼看一眼即可。

> ⚠️ **使用提示**：请遵守所在单位考试规则，风险自担。

---

## 1. 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11（多显示器 + DPI 缩放正常） |
| Python | 3.10+（仅开发/打包需要；打包后 exe 免 Python 环境） |
| 考试载体 | 任意：VMware / 天翼云电脑 / 普通浏览器均可（全屏抓取，不看窗口标题） |
| API | 豆包（火山方舟）或 千问（DashScope）API Key 任选其一 |

## 2. 安装

```bash
cd AnswerBot-Screen
pip install -r requirements.txt
```

## 3. 首次配置（config.json）

编辑程序目录下 `config.json`（UTF-8，缺失字段自动补齐）：

```jsonc
{
  "ai_provider": "qwen",             // doubao / qwen（主识别）
  "dashscope_api_key": "你的千问Key", // 千问 Key（DashScope）
  "ark_api_key": "",                 // 豆包 Key（备选，可留空）
  "capture_monitor": 0,              // 0=所有显示器合并（默认），1..N=指定显示器
  "fallback_interval_ms": 30000,     // 定时兜底识别：距上次识别 30s 强制再识别一次
  "hotkey": "ctrl+alt+h"             // 全局隐藏/显示热键
}
```

Key 只存本机 config.json（已加入 .gitignore）。

## 4. 运行

```bash
python main.py
```

启动后悬浮窗出现在**屏幕 1 右上角**，右下角出现**托盘图标**；程序全屏抓取并开始监听，
考试载体是什么窗口都无所谓（VMware、天翼云电脑、网页版都行）。

## 5. 使用说明

1. 打开考试系统（窗口/全屏模式均可；**独占全屏**除外，见第 9 节排障）。
2. 开始答题后**什么都不用做**：换题画面稳定后自动识别并显示答案。
3. **自动识别漏了也不怕**：每 30 秒强制重新识别一次（饱和式兜底），同一题不重复显示。
4. 悬浮窗可**拖拽**（重启记忆）；**右键悬浮窗**或**托盘图标**：隐藏/暂停/退出。
5. 换题画面相似漏判时，可按**绑定的手动触发键**立即识别（可选，见 6.3）。

## 6. 功能说明

### 6.1 全屏抓取（v2.1）

mss 抓取全屏（默认合并所有显示器，`capture_monitor` 可选指定显示器）。
不依赖窗口标题/句柄，VMware、天翼云电脑等任何载体通用。
检测链路用缩略图（宽 ≤1280）计算，识别链路用原图裁剪。

### 6.2 变化检测 + 题目区域定位（v2.2 块级 pHash）

- **块级判定**：画面分成 4×3 块，每块独立 pHash，任一块变化即触发
  （全屏抓取下题目区占屏比小，全局 pHash 距离被稀释会漏检——小字体翻页实测
  全局距离仅 0~1，块级距离 10~14）；
- 连续 `n_stable` 帧稳定才识别（防动画半帧）；
- **像素差分自动定位题目区域**（`question_roi`）：翻页期间差异区域收敛成矩形，
  杂散小区域（倒计时、状态栏跳动）按面积占比过滤；**悬浮窗自身区域自动排除**
  （差分时不参与，避免"识别→悬浮窗变化→又触发"的死循环）；
- 题目区域自动持久化，下一题沿用（布局稳定时更快更准）；识别只发 ROI 裁剪图。

### 6.3 手动触发（可选，v2.1 降级）

点悬浮窗「绑定按键」→ 按想用的键（鼠标侧键或键盘组合）即绑定。
**注意**：虚拟机/云电脑内鼠标事件可能被客户机截获，主机钩子收不到侧键——
手动触发仅作辅助，**保底以 30s 定时兜底为准**。

### 6.4 定时兜底识别（FR-12，v2.1）

变化检测可能漏判（两题画面相似/检测卡住）→ 距上次识别超过 `fallback_interval_ms`
（默认 30s）自动强制识别一次。结果与上次相同（答案+题目摘要）则不重复显示（去重，
`dedupe_by_answer` 可改为仅比答案）。考试 1 小时最多约 120 次调用，成本可接受。

### 6.5 主备切换

豆包识别失败且已配置 `dashscope_api_key` 时，自动切换千问重试一次。

### 6.6 系统托盘（FR-13，v2.1）

右下角托盘常驻：显示/隐藏悬浮窗、开始/暂停识别、退出。
快捷键隐藏后忘记恢复？点托盘一键找回。

## 7. 参数调优（config.json）

| 键 | 默认 | 说明 |
|---|---|---|
| `capture_monitor` | 0 | 全屏抓取：0=全部显示器合并，1..N=指定显示器 |
| `detect_interval_ms` | 800 | 取帧间隔；越小响应越快、CPU 占用越高 |
| `t_change` | 12 | 废弃（v2.2 起由 t_change_block 替代，保留兼容） |
| `t_change_block` | 5 | 块级变化阈值：任一块帧间距离≥此值触发（字体小/变化小可调低到 4） |
| `grid` | [4,3] | 检测分块 (列,行)：块越小对小变化越敏感，CPU 略增 |
| `t_stable` / `n_stable` | 4 / 6 | 稳定判定（所有块）；翻页动画长可调大 `n_stable` |
| `t_timeout_ms` | 30000 | 变化后最大等待稳定时间 |
| `fallback_interval_ms` | 30000 | 定时兜底识别间隔（0=关闭） |
| `diff_threshold` | 25 | 像素差分阈值 |
| `diff_area_ratio` | 0.005 | 杂散小区域过滤（差异像素占比） |
| `question_roi` | null | 题目区域 [x,y,w,h]（自动学习持久化，可手动改） |
| `overlay_rect` | null | 悬浮窗像素矩形（自动换算，可覆盖微调） |
| `dedupe_by_answer` | true | 去重：true=答案+摘要都同才去重；false=仅答案 |
| `overlay_alpha` | 0.85 | 悬浮窗透明度 |
| `manual_cooldown_ms` | 5000 | 手动触发冷却 |

废弃字段（保留读取但不再使用）：`vm_window_keyword`、`capture_mode`、`mss_monitor`。

## 8. 打包 exe

```bash
pip install pyinstaller
pyinstaller AnswerBot.spec
```

产出 `dist/AnswerBot.exe`（单文件、无控制台窗口），拷到任意 Windows 10/11
机器直接运行（config.json 放 exe 同目录）。

- 杀软可能误报 PyInstaller 产物：加白名单；或改用 `pyinstaller -D -w --name AnswerBot main.py`；
- 打包后日志在 exe 同目录 `logs/answerbot.log`。

## 9. 日志与排障

日志：`logs/answerbot.log`（滚动保留 3 份 × 1MB）。常见问题：

| 现象 | 处理 |
|---|---|
| 一直「识别失败」 | 检查 Key/模型 ID、网络（`dashscope.aliyuncs.com` / `ark.cn-beijing.volces.com`） |
| 全屏抓取黑屏 | 客户端是「独占全屏」模式，mss 抓不到 → 改为「窗口/无边框全屏」模式运行客户端 |
| 同一题反复识别 | 不应出现；出现则调大 `t_change` 或检查 `dedupe_by_answer` |
| 换题后不出答案 | 调小 `detect_interval_ms`；动画长调大 `n_stable`；再不行等 30s 兜底 |
| 识别内容含悬浮窗/倒计时 | 属正常 ROI 排除范围；杂散干扰可调大 `diff_area_ratio` |
| 悬浮窗被监控扫到 | Ctrl+Alt+H 秒隐藏；托盘一键恢复；可调小 `overlay_alpha` |
| 鼠标侧键无反应 | 虚拟机内正常现象，靠 30s 兜底；或绑定键盘组合键 |

## 10. 设计决策（与文档的差异/补充）

- v2.2（实机反馈修订）：块级 pHash 变化检测（解决全屏小字体翻页漏检）、
  ROI 定位改为「变化块区域 + 像素差分」双来源（小文字区域不再被面积过滤丢掉）、
  兜底/手动触发状态行带来源提示；
- v2.1（实机反馈修订）：窗口抓取 → 全屏抓取；ROI 差分定位、30s 饱和式兜底、
  托盘图标、悬浮窗区域排除与置顶强化；侧键手动触发降级为辅助；
- 识别失败也会记录基准帧：同一题画面避免反复重试；
- 日志仅写文件（打包后无控制台）；
- `history.json` 为可选扩展，未启用（架构预留）。

## 11. 项目结构

```
AnswerBot-Screen/
├── main.py              # 入口：DPI-Aware → 日志 → 配置 → 悬浮窗/托盘/监听/主循环
├── screen_capture.py    # 全屏抓取（mss）+ 缩略图 + 黑屏检测（v2.1）
├── change_detector.py   # pHash 状态机 + 像素差分 ROI 收敛 + 杂散过滤（v2.1）
├── ai_client.py         # 豆包/千问视觉 + 容错解析 + 重试
├── answer_overlay.py    # 悬浮窗 + 置顶强化 + get_rect_pixels 排除区换算（v2.1）
├── loop.py              # 主循环 + 定时兜底识别 + 去重（v2.1）
├── input_listener.py    # 手动触发 + 按键录制（引用持有修复，v2.1）
├── tray.py              # 系统托盘（pystray，v2.1 新增）
├── config.py / config.json
├── requirements.txt / AnswerBot.spec / README.md / .gitignore
```

配套文档：《03-读屏方案-项目方案.md》《04-读屏方案-开发交接文档.md》
《05-读屏方案-v2.1修改任务书.md》（项目代号 AnswerBot-Screen）。
