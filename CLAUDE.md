# CLAUDE.md

本项目的完整交接说明见 **[AGENTS.md](AGENTS.md)**（项目定位、模块地图、关键设计决策、踩坑清单、测试与发布流程）。

快速要点：

- Windows 桌面读屏答题工具：全屏抓取 → 块级 pHash 检测 → 视觉大模型 → 置顶悬浮窗显示答案；
- v2.6：**Key 全界面化**——悬浮窗「⚙」/右键/托盘随时打开「设置 API Key」对话框，
  同事粘贴 Key 即可，不用改 config.json；
- v2.5：**DeepSeek 首选**（必须传 `thinking={"type":"disabled"}`）、三家模型可在界面切换、
  首次运行弹图形配置向导（`setup_wizard.py`）、单屏下窗口位置越界会自动召回主屏；
- 运行：`python main.py`；打包：`pyinstaller AnswerBot.spec`；
- `config.json` 含 API Key，**已在 .gitignore，严禁提交**；
- 核心设计不要轻易推翻：块级 pHash（全屏下全局距离被稀释）、悬浮窗区域全链路排除、
  pynput 触发回调必须异步（Windows 钩子超时会被移除）、答案归一化只取开头选项字母。
