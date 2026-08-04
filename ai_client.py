# -*- coding: utf-8 -*-
"""AI 识别模块（FR-05）：豆包（火山方舟）主 / 通义千问 VL 备，OpenAI 兼容接口。

- 高清截图压缩（1280px / JPEG85 / base64）后发送；
- 期望输出 JSON {question, options, answer, confidence}；
- 容错解析 parse_result：JSON 解析失败 → 提取花括号片段 → 正则兜底；
- 重试 3 次，2s/4s/8s 指数退避，单次请求 60s 超时；
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

# 各提供方 Endpoint（文档 6 节）
ENDPOINTS = {
    "doubao": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
}

SYSTEM_PROMPT = (
    "你是一个答题助手。用户发来考试题目截图，请识别题目和选项，"
    "并给出最准确的答案。只输出 JSON，不要输出任何其他内容。"
    "JSON 格式：{\"question\": \"题目全文\", \"options\": [\"A. ...\", \"B. ...\"], "
    "\"answer\": \"A\", \"confidence\": 0.9}。"
    "answer 为选项字母（如 A/B/C/D）或简短答案文本；confidence 为 0~1 的把握度。"
)


class AiError(Exception):
    """AI 识别失败（网络/鉴权/解析）。"""


def compress(img, max_w: int = 1280, quality: int = 85) -> str:
    """压缩为 JPEG base64 字符串（宽超 1280 等比缩小）。"""
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def parse_result(content: str) -> dict:
    """容错解析 AI 返回内容 → {question, options, answer, confidence}。

    顺序：json.loads → 提取花括号片段 → 正则兜底（答案[:：]X / 题目[:：]…）。
    全失败时整段原文作 question，answer 置空（悬浮窗显示原文）。
    """
    result = {"question": "", "options": [], "answer": "", "confidence": None}
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
    m = re.search(r"答案\s*[:：]\s*([A-Ha-h])", text)
    if m:
        result["answer"] = m.group(1).upper()
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
    ans = _get("answer", "答案", "Answer")
    opts = data.get("options") or data.get("选项") or []
    if not isinstance(opts, list):
        opts = []
    conf = data.get("confidence") or data.get("把握度")
    # answer 可能是 "A"、"A. xxx"、"A：xxx"，归一化
    if isinstance(ans, str):
        m = re.match(r"\s*([A-Ha-h])", ans)
        if m:
            ans = m.group(1).upper()
    return {"question": str(q), "options": opts, "answer": str(ans), "confidence": conf}


def ask_ai(cfg: dict, img, provider: str | None = None) -> dict:
    """调用视觉大模型识别题目。返回 {question, options, answer, confidence}。

    参数 provider 覆盖配置（主备切换时使用）；失败重试 3 次后退避抛 AiError。
    """
    provider = provider or cfg.get("ai_provider", "doubao")

    if provider == "doubao":
        key = cfg.get("ark_api_key", "")
        model = cfg.get("ark_model", "")
        if not key or not model:
            raise AiError("豆包未配置：请在 config.json 填写 ark_api_key 与 ark_model（接入点 ID）")
    else:
        key = cfg.get("dashscope_api_key", "")
        model = cfg.get("qwen_model", "qwen-vl-plus")
        if not key:
            raise AiError("千问未配置：请在 config.json 填写 dashscope_api_key")

    url = ENDPOINTS[provider]
    b64 = compress(img)
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别这张图片中的题目，并给出答案。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            },
        ],
        "temperature": 0.2,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }

    last_err = None
    for attempt in range(3):  # 重试：2s/4s/8s 退避
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
            log.info("AI 识别成功 provider=%s question_len=%d answer=%s",
                     provider, len(result.get("question", "")), result.get("answer"))
            return result
        except AiError:
            raise  # 解析失败不重试（重试无意义）
        except Exception as e:
            last_err = e
            if attempt == 2:
                break
            time.sleep(2 * (2 ** attempt))

    raise AiError(f"AI 请求失败（已重试 3 次）: {last_err}")


if __name__ == "__main__":
    # 自测（无 Key 时验证配置检查；有 Key 时可用本地图片实测）
    import config as cfg_mod

    c = cfg_mod.load()
    from PIL import Image

    dummy = Image.new("RGB", (800, 600), (255, 255, 255))
    print("compress 长度:", len(compress(dummy)))

    # 容错解析测试
    cases = [
        '{"question": "1+1=?", "options": ["A. 1", "B. 2"], "answer": "B", "confidence": 0.99}',
        '好的，答案如下：{"answer": "C"} 其他内容',
        "答案：A\n题目：以下哪个是…",
        "完全无法解析的内容",
    ]
    for s in cases:
        r = parse_result(s)
        print("解析:", s[:30], "→", r)

    try:
        ask_ai(c, dummy)
    except AiError as e:
        print("配置检查 OK（无 Key 时报错符合预期）:", e)
