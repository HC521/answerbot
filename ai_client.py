# -*- coding: utf-8 -*-
"""AI 识别模块（FR-05）：DeepSeek 首选 / 千问（DashScope）/ 豆包（火山方舟）备选。

v2.5 变更：
- 新增 DeepSeek 视觉识别（`deepseek-flash`）。DeepSeek 新接口的 flash 是推理模型，
  默认会先输出一大段 reasoning 占满 token，因此**必须传 thinking={"type":"disabled"}**
  关掉思考链（实测：开启 8~10s 且 content 为空；关闭后 1.4~1.8s 正常返回）；
- provider 可在程序界面切换（悬浮窗「模型」菜单 / 托盘），主识别 + 备用链共用一套实现；
- 答案归一化修正：模型返回 "B. TCP" 这类「字母+选项内容」时只取开头字母
  （旧实现会把 B/T/C/P 全扫成字母，得到错误的多选答案）。

公共约定：
- 输入：config dict + PIL 图片；
- 输出：{question, type, options, answer, confidence, provider, provider_label, elapsed}
- 容错解析：JSON → 花括号片段 → 正则兜底；结果为空/无答案视为失败；
- 截图仅内存处理，不落盘（NFR-04）。
"""

import base64
import io
import json
import logging
import re
import time

import requests

log = logging.getLogger("ai_client")

# ---------------------------------------------------------------------------
# 提供方元数据（v2.5：DeepSeek 加入并置首）
# ---------------------------------------------------------------------------
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "key_field": "deepseek_api_key",
        "model_field": "deepseek_model",
        "default_model": "deepseek-flash",
        "order": 0,
    },
    "qwen": {
        "label": "千问",
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "key_field": "dashscope_api_key",
        "model_field": "qwen_model",
        "default_model": "qwen-vl-max",
        "order": 1,
    },
    "doubao": {
        "label": "豆包",
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "key_field": "ark_api_key",
        "model_field": "ark_model",
        "default_model": "",
        "order": 2,
    },
}

ENDPOINTS = {pid: p["endpoint"] for pid, p in PROVIDERS.items()}

# 各提供方默认主模型（config 里留空时使用）
DEFAULT_MODELS = {pid: p["default_model"] for pid, p in PROVIDERS.items()}

SYSTEM_PROMPT = (
    "你是一个答题助手。用户发来考试题目截图，请仔细阅读题干和全部选项后作答。\n"
    "第一步：识别题型——题干或选项区含「多选/多项/不定项/至少/正确选项有」等字样为多选；"
    "题干为陈述句且无 A/B/C/D 选项为判断题；否则为单选。\n"
    "第二步：作答。\n"
    "只输出 JSON，不要输出任何其他内容。JSON 格式："
    "{\"question\": \"题目全文\", \"type\": \"single|multiple|judge\", "
    "\"options\": [\"A. ...\", \"B. ...\"], \"answer\": \"...\", \"confidence\": 0.9}\n"
    "answer 规则：\n"
    "- 单选题：单个选项字母，如 \"B\"；\n"
    "- 多选题：全部正确选项字母连续书写，如 \"ACD\"（不要用逗号/顿号分隔）；\n"
    "- 判断题：\"对\" 或 \"错\"（也可写 \"正确\"/\"错误\"）；\n"
    "- 拿不准时凭领域知识选最可能正确的，不要留空。"
    "confidence 为 0~1 的把握度。"
)


class AiError(Exception):
    """AI 识别失败（网络/鉴权/解析）。"""


# ---------------------------------------------------------------------------
# 提供方状态 / 顺序
# ---------------------------------------------------------------------------
def provider_label(pid: str) -> str:
    """提供方显示名（未知返回原 id）。"""
    return (PROVIDERS.get(pid) or {}).get("label", pid)


def provider_model(cfg: dict, pid: str) -> str:
    """该提供方在 config 里配置的模型；空则用默认。"""
    meta = PROVIDERS.get(pid)
    if not meta:
        return ""
    return (cfg.get(meta["model_field"]) or meta["default_model"] or "").strip()


def is_provider_configured(cfg: dict, pid: str) -> bool:
    """是否已配置（Key 有值；豆包还要求模型/接入点 ID）。"""
    meta = PROVIDERS.get(pid)
    if not meta:
        return False
    if not (cfg.get(meta["key_field"]) or "").strip():
        return False
    if not provider_model(cfg, pid):
        return False
    return True


def configured_providers(cfg: dict) -> list[str]:
    """已配置的提供方，按「首选在前 + DeepSeek > 千问 > 豆包」排序。"""
    return [pid for pid in provider_order(cfg) if is_provider_configured(cfg, pid)]


def provider_order(cfg: dict) -> list[str]:
    """识别尝试顺序：config 的首选 → DeepSeek → 千问 → 豆包。"""
    primary = (cfg.get("ai_provider") or "deepseek").strip()
    order = [primary] if primary in PROVIDERS else []
    for pid in sorted(PROVIDERS, key=lambda p: PROVIDERS[p]["order"]):
        if pid not in order:
            order.append(pid)
    return order


def provider_status(cfg: dict) -> list[dict]:
    """供 UI 展示：每个提供方的 id/显示名/是否可用/模型。"""
    out = []
    for pid in provider_order(cfg):
        out.append({
            "id": pid,
            "label": provider_label(pid),
            "configured": is_provider_configured(cfg, pid),
            "model": provider_model(cfg, pid),
        })
    return out


# ---------------------------------------------------------------------------
# 图片压缩 / 解析
# ---------------------------------------------------------------------------
def compress(img, max_w: int = 1600, quality: int = 90) -> str:
    """压缩为 JPEG base64 字符串（宽超 max_w 等比缩小）。

    v2.4：max_w 1280→1600、quality 85→90——考试题目字号小，
    提高发送分辨率与画质可明显改善小字识别正确率（token 成本略有上升）。
    """
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


_JUDGE_MAP = {"√": "对", "×": "错", "✓": "对", "✗": "错",
              "正确": "对", "错误": "错", "是": "对", "否": "错",
              "T": "对", "F": "错", "true": "对", "false": "错"}

# 合法答案：选项字母串（A~H，最多 8 个）或 对/错。
# v2.5：模型有时会返回 "无法识别题目" 这类话——旧实现只看「answer 非空」就当成功，
# 会把这种话直接显示到答案区。必须严格校验，否则考试中会顶掉上一题的真答案。
_VALID_ANSWER = re.compile(r"^(?:[A-H]{1,8}|对|错)$")


def is_valid_answer(ans: str) -> bool:
    """答案是否合法（字母串 / 对 / 错）。"""
    return bool(ans) and bool(_VALID_ANSWER.match(ans.strip()))


def _normalize_answer(ans) -> str:
    """答案归一化（v2.5 修正）。

    - "B" / "ACD" / "A、C、D" → 字母串；
    - "B. TCP" / "答案：B" / "选 B" → 只取开头字母（旧实现会误取 B/T/C/P）；
    - "对" / "√" / "正确" → "对"；"错" / "×" → "错"。
    """
    if not isinstance(ans, str):
        return str(ans or "")
    text = ans.strip()
    if not text:
        return ""

    # 允许「答案：」「选」「【】」等前缀，只取其后连续的选项字母
    m = re.match(
        r"^[\s【\[(（:：,，、]*(?:答案|正确选项|选项|答案选项|应选|选)?[\s:：=是为]*"
        r"([A-Ha-h](?:[\s,，、/|+]*[A-Ha-h])*)",
        text,
    )
    if m:
        letters = re.sub(r"[^A-Ha-h]", "", m.group(1)).upper()
        if letters:
            return letters

    # 判断题文本
    compact = re.sub(r"[\s。.！!]+$", "", text)
    if compact in _JUDGE_MAP:
        return _JUDGE_MAP[compact]
    lower = compact.lower()
    if lower in _JUDGE_MAP:
        return _JUDGE_MAP[lower]
    # "答案：对" / "判断题：正确" 这类
    for key, val in _JUDGE_MAP.items():
        if compact.endswith(key) and len(compact) > len(key):
            return val
    return compact


def parse_result(content: str) -> dict:
    """容错解析 AI 返回内容 → {question, options, answer, confidence}。

    顺序：json.loads → 提取花括号片段 → 正则兜底（答案[:：]X / 题目[:：]…）。
    全失败时整段原文作 question，answer 置空（悬浮窗显示原文）。
    """
    result = {"question": "", "type": "", "options": [], "answer": "",
              "confidence": None}
    text = (content or "").strip()

    # 1) 直接 JSON 解析
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _pick(data)
    except Exception:
        pass

    # 2) 提取花括号片段（模型可能夹杂解释文字）
    for m in re.finditer(r"\{[^{}]*\}", text):
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return _pick(data)
        except Exception:
            continue

    # 3) 正则兜底
    m = re.findall(r"答案\s*[:：]\s*([A-Ha-h、,\s]+)", text)
    if m:
        result["answer"] = re.sub(r"[^A-Ha-h]", "", m[0]).upper()
    else:
        m2 = re.findall(r"(?:答案|正确选项)\s*[:：]\s*(对|错|正确|错误)", text)
        if m2:
            result["answer"] = _normalize_answer(m2[0])
    m = re.search(r"题目\s*[:：]?\s*(.{1,200}?)(?:\n|$)", text, re.S)
    if m:
        result["question"] = m.group(1).strip()
    if not result["question"]:
        result["question"] = text[:200]  # 原文摘要
    return result


def _pick(data: dict) -> dict:
    """从 JSON dict 提取标准字段（兼容大小写/别名）。"""
    def _get(*keys):
        for k in keys:
            if k in data and data[k] is not None:
                return data[k]
        return ""

    q = _get("question", "题目", "Question")
    ans = _normalize_answer(_get("answer", "答案", "Answer"))
    opts = data.get("options") or data.get("选项") or []
    if not isinstance(opts, list):
        opts = []
    conf = data.get("confidence") or data.get("把握度")
    return {"question": str(q), "type": str(_get("type", "题型") or ""),
            "options": opts, "answer": str(ans), "confidence": conf}


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------
def _provider_credentials(cfg: dict, pid: str) -> tuple[str, str]:
    """取 (key, model)；未配置抛 AiError（中文提示）。"""
    meta = PROVIDERS.get(pid)
    if not meta:
        raise AiError(f"未知的模型提供方: {pid}")
    key = (cfg.get(meta["key_field"]) or "").strip()
    model = provider_model(cfg, pid)
    if not key:
        raise AiError(f"{meta['label']}未配置：请在 config.json 填写 {meta['key_field']}"
                      f"（或用悬浮窗「模型」菜单切换/首次运行向导填写）")
    if not model:
        raise AiError(f"{meta['label']}未配置模型：请在 config.json 填写 {meta['model_field']}")
    return key, model


def _build_body(pid: str, model: str, b64: str) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别这张图片中的题目，并给出答案。"},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            },
        ],
        "temperature": 0.2,
        "max_tokens": 800,
        "response_format": {"type": "json_object"},
    }
    if pid == "deepseek":
        # DeepSeek flash 是推理模型：不关思考链会耗光 token 且 content 为空（v2.5 实测）
        body["thinking"] = {"type": "disabled"}
        body["max_tokens"] = 600
    return body


def ask_ai(cfg: dict, img, provider: str | None = None, attempts: int = 3) -> dict:
    """调用指定（或 config 首选）视觉大模型识别题目。

    返回 {question, type, options, answer, confidence, provider, provider_label, elapsed}。
    失败重试 `attempts` 次后退避抛 AiError。
    """
    pid = (provider or cfg.get("ai_provider") or "deepseek").strip()
    key, model = _provider_credentials(cfg, pid)
    url = ENDPOINTS[pid]
    b64 = compress(img)
    body = _build_body(pid, model, b64)

    label = provider_label(pid)
    last_err = None
    t0 = time.time()
    for attempt in range(max(1, attempts)):
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                json=body,
                timeout=(10, 60),  # connect 10s / read 60s（坑 9）
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            result = parse_result(content)
            if not result.get("question") and not result.get("answer"):
                raise AiError("AI 返回内容无法解析出题目/答案")
            if not result.get("answer"):
                # 模型返回了内容但没给答案（胡答/图片无题）→ 视为失败，
                # 不能当成功显示"（识别失败）"占位
                snippet = (content or "")[:80].replace("\n", " ")
                raise AiError(f"未识别出答案（模型返回: {snippet}…）")
            if not is_valid_answer(result["answer"]):
                # v2.5：模型可能回 "无法识别题目" 之类的散文，必须当失败处理
                snippet = (content or "")[:80].replace("\n", " ")
                raise AiError(f"答案是无效格式「{result['answer'][:20]}」（模型返回: {snippet}…）")
            result["provider"] = pid
            result["provider_label"] = label
            result["elapsed"] = round(time.time() - t0, 2)
            log.info("AI 识别成功 provider=%s model=%s %.2fs question_len=%d answer=%s",
                     pid, model, result["elapsed"],
                     len(result.get("question", "")), result.get("answer"))
            return result
        except AiError:
            raise  # 解析/配置类错误不重试（重试无意义）
        except Exception as e:
            last_err = e
            log.warning("AI 请求失败 provider=%s 第%d次: %s", pid, attempt + 1, e)
            if attempt >= attempts - 1:
                break
            time.sleep(2 * (2 ** attempt))   # 2s / 4s / 8s 退避

    raise AiError(f"{label}请求失败（已重试 {attempts} 次）: {last_err}")


def ask_ai_chain(cfg: dict, img) -> dict:
    """主识别 + 备用链：按 provide_order 依次尝试已配置的提供方。

    返回首个成功结果（含 provider / provider_label / elapsed）。
    全部失败抛 AiError（错误信息汇总每个提供方的原因）。
    """
    order = configured_providers(cfg)
    if not order:
        errs = "、".join(f"{provider_label(p)}(未配置)" for p in provider_order(cfg))
        raise AiError(f"没有任何可用的模型 Key：{errs}。请在首次配置向导或 config.json 中填写")

    errors = []
    for pid in order:
        try:
            return ask_ai(cfg, img, provider=pid, attempts=2)
        except AiError as e:
            errors.append(f"{provider_label(pid)}: {e}")
            log.warning("提供方 %s 失败，尝试下一个: %s", pid, e)
    raise AiError("全部模型失败 -> " + "；".join(errors))


# ---------------------------------------------------------------------------
# 首次配置向导用：连通性自检
# ---------------------------------------------------------------------------
def test_connection(provider: str, key: str, model: str) -> tuple[bool, str]:
    """用纯文本请求校验 Key/模型是否可用（不发送截图）。返回 (ok, 说明)。"""
    meta = PROVIDERS.get(provider)
    if not meta:
        return False, f"未知提供方: {provider}"
    key = (key or "").strip()
    model = (model or meta["default_model"] or "").strip()
    if not key:
        return False, "API Key 为空"
    if not model:
        return False, "模型 ID 为空（豆包需填火山方舟接入点 ID，如 ep-xxxx）"

    body = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复两个字：收到"}],
        "temperature": 0,
        "max_tokens": 16,
        "stream": False,
    }
    if provider == "deepseek":
        body["thinking"] = {"type": "disabled"}
    try:
        t0 = time.time()
        r = requests.post(meta["endpoint"],
                          headers={"Authorization": f"Bearer {key}"},
                          json=body, timeout=(10, 40))
        dt = time.time() - t0
        if r.status_code == 200:
            return True, f"连接成功（{dt:.1f}s，模型 {model}）"
        detail = ""
        try:
            detail = (r.json().get("error") or {}).get("message", "")
        except Exception:
            detail = r.text[:120]
        hint = {
            401: "Key 无效或已过期",
            403: "无权限（确认 Key 与该模型开通状态）",
            404: "模型不存在（检查模型 ID）",
            429: "额度/频率超限",
        }.get(r.status_code, "")
        return False, f"HTTP {r.status_code} {hint} {detail}".strip()
    except Exception as e:
        return False, f"网络异常: {e}"


if __name__ == "__main__":
    # 自测：python ai_client.py（无 Key 时验证配置检查；有 Key 时可实测）
    import config as cfg_mod
    from PIL import Image

    c = cfg_mod.load()
    dummy = Image.new("RGB", (800, 600), (255, 255, 255))
    print("compress 长度:", len(compress(dummy)))
    print("提供方顺序:", provider_order(c), "已配置:", configured_providers(c))

    # 答案归一化测试（v2.5 修正点）
    cases = [
        ("B", "B"), ("ACD", "ACD"), ("A、C、D", "ACD"), ("B. TCP", "B"),
        ("答案：C", "C"), ("选 A", "A"), ("对", "对"), ("√", "对"),
        ("错误", "错"), ("正确", "对"), ("AC", "AC"),
    ]
    for src, want in cases:
        got = _normalize_answer(src)
        flag = "OK " if got == want else "FAIL"
        print(f"  {flag} {src!r} -> {got!r} (期望 {want!r})")
        assert got == want, (src, got, want)

    # 合法答案校验（v2.5：防止"无法识别题目"被当答案显示）
    for src, want in [("A", True), ("ABD", True), ("对", True), ("错", True),
                      ("无法识别题目", False), ("", False), ("BCDEFGHI", False)]:
        got = is_valid_answer(src)
        flag = "OK " if got == want else "FAIL"
        print(f"  {flag} 校验 {src!r} -> {got} (期望 {want})")
        assert got == want, (src, got, want)

    # 容错解析测试
    for s in [
        '{"question": "1+1=?", "options": ["A. 1", "B. 2"], "answer": "B", "confidence": 0.99}',
        '好的，答案如下：{"answer": "C"} 其他内容',
        "答案：A\n题目：以下哪个是…",
        "完全无法解析的内容",
    ]:
        print("解析:", s[:30], "→", parse_result(s)[:3] if False else parse_result(s)["answer"])

    try:
        ask_ai(c, dummy)
    except AiError as e:
        print("配置检查 OK（无 Key 时报错符合预期）:", e)
