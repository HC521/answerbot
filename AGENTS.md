# AGENTS.md — AnswerBot-Screen 交接说明（给后续 AI Agent）

> 你正在接手一个**已完成并通过实机测试迭代 5 轮**的 Windows 桌面工具。
> 读本文即可继续工作，无需追问历史。配套文档：`README.md`（面向用户）、
> `05-读屏方案-v2.1修改任务书.md`（v2.1 增量需求原文）。

---

## 1. 项目是什么（以及边界）

**AnswerBot-Screen**：个人自用的读屏答题辅助工具。工作方式：

```
全屏抓取（mss） → 块级 pHash 变化检测 → 像素差分定位题目 ROI
→ 视觉大模型识别（DeepSeek 首选 / 千问 / 豆包可切）→ 置顶半透明悬浮窗显示答案（屏幕 1）
```

**硬约束（改代码前必读）**：
- **只读屏，绝不修改/注入考试客户端**（不碰 VMware/云电脑进程，不模拟点击考试界面）；
- 答案只显示在本机悬浮窗，不发送到外部（除 AI API）；
- 个人过渡方案，不追求商品化；不引入需要账号/云服务的重型依赖；
- Python 3.10+ / Windows 10+ 单文件 exe（PyInstaller）；
- **分发形态**（v2.5 起）：exe + `使用说明.txt` + `test_quiz.html`，同事首次打开用图形向导填自己的 Key。

## 2. 快速开始

```bash
pip install -r requirements.txt          # pywin32 Pillow numpy requests mss keyboard pynput pyinstaller pystray
# config.json 含 API Key（已 gitignore）。没有 Key 时首次运行会弹图形配置向导（setup_wizard.py）
python main.py                            # 开发运行
pyinstaller AnswerBot.spec                # 打包 → dist/AnswerBot.exe（单文件无控制台）
```

`config.json` 关键字段：`ai_provider`（deepseek/qwen/doubao，界面可切）、
`deepseek_api_key` + `deepseek_model`（默认 `deepseek-flash`）、`dashscope_api_key`、
`qwen_model`、`ark_api_key`/`ark_model`、`fallback_interval_ms`（30s 兜底）、`t_change_block`（5）。

## 3. 模块地图（~1900 行，10 个模块）

| 文件 | 职责 | 关键点 |
|---|---|---|
| `main.py` | 入口 | DPI-Aware 必须最先调用；日志写 `logs/answerbot.log`；启动顺序：**首次配置向导（无常 Key 时）** → overlay → listener → tray → loop 线程 → tkinter mainloop |
| `setup_wizard.py` | 首次配置向导（v2.5） | 独立 `tk.Tk()`，销毁后才创建 overlay（避免双 root）；选提供方/填 Key/`test_connection` 测试/写 config；「稍后配置」可跳过 |
| `screen_capture.py` | 全屏抓取 | `capture_fullscreen(monitor=0)` 合并全部显示器；`thumbnail()` 宽≤1280 供检测；`crop_pixels()` ROI 裁剪；`mask_rect()` 涂白悬浮窗；`monitor_origin()` / `virtual_screen_rect()`（v2.5，窗口位置校验）|
| `change_detector.py` | 变化检测（核心） | **块级 pHash**（grid 4×3，任一块帧间距离≥`t_change_block` 即触发）；`diff_region()` 像素差分定位 ROI；`_ignored_block_mask()` 悬浮窗块排除；状态机 IDLE→CHANGING→STABLE/TIMEOUT |
| `ai_client.py` | AI 识别 | `PROVIDERS` 元数据 + `ask_ai(provider=)` + `ask_ai_chain()`（首选+备用链）；DeepSeek 必须 `thinking={"type":"disabled"}`；`_pick()`/`_normalize_answer()` 只取开头选项字母（兼容 `B. TCP`）、判断题归一化（√→对）；`compress()` 1600px/JPEG90；重试 2/4s 退避 |
| `answer_overlay.py` | 悬浮窗 | tkinter 无边框置顶；按钮行：**模型切换菜单**/隐藏/暂停/绑定按键/**保底**；右键菜单（模型/窗口移回主屏/退出）；`get_rect_pixels()` 给检测排除用；`_place_default()` 位置越界自动召回（v2.5）；`show()/set_status()` 内部 `root.after(0,...)` 线程安全 |
| `loop.py` | 主循环 | 独立线程；变化触发/定时兜底/手动触发统一走 `_recognize()`（`_lock`+`_busy` 互斥）；结果去重；`set_provider()` 切换首选并持久化；**识别失败保留上次答案只更新状态行**；兜底计时初始化为启动时刻（v2.5）|
| `input_listener.py` | 手动触发/按键录制 | pynput 监听；**触发回调必须异步线程**（见 §6 坑 1）；录制时拒绝鼠标左键；Esc 取消 |
| `tray.py` | 系统托盘 | pystray 独立线程；菜单：显示隐藏/暂停/**模型切换（单选）**/**窗口移回主屏**/退出；tkinter 操作经 `root.after` 调度 |
| `config.py` | 配置读写 | `load()` 用 DEFAULT_CONFIG 补缺失字段；`set()` **全量写回**（注意 §6 坑 6）|

数据流细节：`loop.run()` 每 `detect_interval_ms` 抓一帧原图 + 缩略图 → `feed(thumb, exclude_rects)` →
STABLE 时 `_recognize(img, thumb)`：ROI（缩略图坐标）换算回原图坐标裁剪 → 无 ROI 则全图但涂白悬浮窗 →
`ask_ai_chain()` → 去重 → `overlay.show()`（状态行带 provider/耗时）→ `detector.set_base(thumb)` 抑制同题重复触发。

## 4. 关键设计决策（**为什么这样写，勿轻易推翻**）

1. **全屏抓取而非窗口抓取**（v2.1）：考试载体不固定（VMware/天翼云电脑/网页），窗口标题不可靠。
2. **块级 pHash 而非全局**（v2.2）：全屏下题目区占屏比小，全局 pHash 距离被稀释——**实测小字体翻页全局距离仅 0~1（完全漏检），块级距离 10~14**。这是 v2.2 的核心，改回全局必然复现"翻页不识别"。
3. **ROI 双来源**：变化块区域 + 像素差分。像素差分有面积过滤（防倒计时等杂散），小字题目区可能被滤掉，故变化块区域兜底。
4. **悬浮窗区域全链路排除**（v2.3）：检测块忽略、ROI 跳过、全图识别涂白。否则悬浮窗显示答案会触发"识别到悬浮窗自身"的死循环。
5. **触发回调异步线程**（v2.4）：pynput 回调跑在 Windows 低级钩子线程，阻塞（抓屏+AI 10s）会被系统**超时移除钩子**→ 绑定键永久失效。任何钩子回调里禁止耗时操作。
6. **识别失败保留上次答案**（v2.2）：失败只更新状态行，不清空答案区——考试中屏幕上必须始终有可用答案。
7. **定时兜底 + 去重**（v2.1）：变化检测可能漏判，30s 强制识别一次；结果相同不重复显示。
8. **题型识别 + 多选全字母**（v2.4）：多选答案 `ABD` 完整输出（旧版只取首字母）；判断题 `对/错`。
9. **DeepSeek 设为首选**（v2.5）：`deepseek-flash` 多模态实测 1.4~1.9s/题（正确率不输 qwen-vl-max，
   速度快 3~5 倍），界面可一键切回千问/豆包。**必须传 `thinking={"type":"disabled"}`**——
   实测不传时模型先输出 2000+ token 思考链、`content` 为空且耗时 8~10s。
10. **答案归一化只取开头字母**（v2.5）：模型常返回 `"B. TCP"`，旧实现 `re.findall([A-H])`
    会得到 `BTCP`；现在用「开头连续选项字母」正则 → `B`。这是换模型后必须回归的点。
11. **窗口位置必须校验**（v2.5）：`overlay_pos` 是绝对坐标，曾经接过副屏的开发机换单屏后，
    保存的 x=1935 落在 1792 宽的屏幕外 → 窗口"消失"只剩托盘。启动时校验
    「≥60% 面积落在虚拟桌面内」，否则召回主屏；托盘还有「窗口移回主屏」兜底。

## 5. 测试方法（无真实考试环境时的做法）

- **合成帧端到端**（推荐）：构造 `FakeCapture` 返回"2560×1440 全屏 + 中央考试窗 + 每 N 次调用翻页"的帧序列，
  monkeypatch `loop.capture` 与 `loop.ask_ai`，跑 `lp.run()` 线程验证多页识别；
  **字体要够大**（46px）：1280 缩略图 → 4×3 块 → 每块 32×32 pHash，字太小会被降采样抹平导致距离为 0（假漏检）；
  只换题干一行、选项不变时也可能碰撞（实测 q2/q3 距离 0），造帧时让选项也变；
- **真机 API 小脚本**：`ai_client.ask_ai(cfg, img)` 直接喂一张生成的题目图，验证答案/耗时；
  `ai_client.test_connection(provider, key, model)` 验证 Key 可用（纯文本请求，不花图片 token）；
- **窗口可见性**：`FindWindowW(None, "AnswerBot")` + `GetWindowRect` 判断是否落在屏幕内（单屏/多屏都要测）；
- **打包冒烟**：`pyinstaller AnswerBot.spec` 后启动 exe 8 秒确认存活 + `dist/logs/answerbot.log` 正常；
  删掉同目录 `config.json` 再启动，应弹出首次配置向导；
- ⚠️ **不要用真实屏幕做端到端**：开发机桌面有其他窗口（浏览器/编辑器）时，全屏抓取会抓到无关内容，测试结论不可信（曾因此误判"检测失效"）。

## 6. 踩过的坑（改代码前必看）

1. **Windows 低级钩子超时**：pynput 回调阻塞 >约 1s 会被系统移除钩子（现象：绑定键只起效一次）。所有回调异步化。
2. **独占全屏抓不到**：云电脑/VMware 用独占全屏时 mss 黑屏；需改「窗口/无边框全屏」模式（README 排障已注明）。
3. **tkinter 按钮失效**：给按钮 `bind("<Button-1>", lambda e: "break")` 会阻断按钮 class 绑定 → 按钮点不动。正确做法：root 上的拖拽回调里判断 `event.widget` 跳过按钮。
4. **无边框窗口无系统关闭钮**：`overrideredirect(True)` 后必须自供退出入口（右键菜单/托盘）。
5. **pynput Listener 被 GC 回收**：必须持有引用（模块级 `_listener_hold` + 实例属性双保险）。
6. **配置污染**：`config.load()` 返回的 dict 若被直接修改，`config.set()` 全量写回会把测试值持久化（曾把 `fallback_interval_ms=1000`、`question_roi` 写进正式配置）。
7. **DPI**：`SetProcessDpiAwareness(2)` 必须在创建任何窗口/抓屏前调用，否则坐标错位。
8. **构建产物勿入库**：`.gitignore` 已含 `config.json`（含 Key）、`*.zip`、`dist/`、`logs/`、`*.exe`、发布包解压目录 `demo/ 2.2/ 2.3/ 2.*/`。**严禁提交 config.json 或 exe**。
9. **两个全局钩子库并存**：`keyboard`（热键）与 `pynput`（手动触发）各装一个低级钩子，互不影响但排查时要分清。
10. **DeepSeek 推理模型**：`deepseek-flash` / `deepseek-v4-pro` 都是推理模型；不关思考链会耗光 token 且 content 为空。
    `deepseek-v4-pro` 目前**不支持图片输入**（prompt_tokens 不含图像，会回"无法读取图片"），别选它做视觉识别。
11. **pip 装依赖**：清华镜像缺 `pywin32`（报 "versions: none"），用**阿里云镜像** `https://mirrors.aliyun.com/pypi/simple/`。
12. **Git 推送**：本机实测 GitHub **直连可推送**（`git ls-remote` 2.6s），不需要代理；网页端慢（约 11s）但 git 的 HTTPS 通道正常。
    GCM（Git Credential Manager）已存凭据，push 自动带鉴权。
13. **GitHub Web 浏览器授权很慢/转圈**：GCM 浏览器授权可能长时间转圈；重试或改用 PAT 粘贴。已授权过的本机直接 `git push` 即可。

## 7. 版本历史

| 版本 | 变更 |
|---|---|
| v1.0 | 初版：窗口抓取(PrintWindow/BitBlt/mss 降级) + pHash + 豆包/千问 + 悬浮窗 |
| v2.1 | 实机反馈修订：**全屏抓取**、ROI 差分定位、30s 定时兜底+去重、悬浮窗置顶强化、**系统托盘**、侧键降级 |
| v2.2 | **块级 pHash**（grid 4×3 + t_change_block）、ROI 双来源、兜底/手动状态行来源提示 |
| v2.3 | **保底按钮**（悬浮窗右上角点击即识别）、检测/ROI/识别全链路排除悬浮窗区域 |
| v2.4 | **多选/判断题支持**（ABD、对/错）、默认模型 qwen-vl-max + 1600px/JPEG90、**修复绑定键只起效一次**（钩子超时） |
| v2.5 | **DeepSeek 多模态接入并设首选**（thinking 关闭，1.4~1.9s）、**三家模型界面可切换**（悬浮窗/托盘/右键，持久化）、**首次运行图形配置向导**（方便同事分发）、**修复单屏下窗口跑到屏外只剩托盘**、修复首轮兜底误触发与 busy 期间日志刷屏、答案归一化修正（`B. TCP`）、`set_base` 改用缩略图 |

## 8. 已知限制 / 待办候选

- 判断题若一屏多题，模型只答第一题（一次识别一题是设计前提）；
- `history.json`（历史记录）为预留扩展未启用；
- 托盘菜单勾选态在切换模型后不会实时刷新（悬浮窗菜单会），重启后正确；
- ROI 为块级粗粒度（4×3 网格），更细网格会增加 CPU；
- 无自动化测试套件（测试均为临时脚本验证后删除，见 §5 方法可重建）。

## 9. 发布流程

```bash
pyinstaller AnswerBot.spec            # → dist/AnswerBot.exe
# 发布包 = exe + 使用说明.txt + test_quiz.html（zip，勿入库）
# 注意：config.json 不要打进发布包——同事首次启动用向导填自己的 Key
```
`test_quiz.html` 是本地模拟考试页（标题含 "VMware"，打开后配合程序自测）。
