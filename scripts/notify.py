"""
推送报告到 Telegram 和飞书
- 头部汇总表 + 每标的详细卡片
- 使用 DeepSeek 翻译英文内容为中文
- 飞书支持签名校验
"""

import os
import sys
import json
import re
import time
import hmac
import hashlib
import base64
import requests
from pathlib import Path


# ── DeepSeek 翻译 ─────────────────────────────────────────
_translation_cache = {}

def translate_to_chinese(text: str, max_len: int = 1500) -> str:
    """使用 DeepSeek 将英文翻译为中文，带简单缓存"""
    if not text or not text.strip():
        return text

    # 截断过长文本（DeepSeek 按 token 计费）
    text = text[:max_len]

    # 检查缓存
    cache_key = hash(text)
    if cache_key in _translation_cache:
        return _translation_cache[cache_key]

    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("⚠️ 没有 DeepSeek API Key，跳过翻译")
        return text

    # 如果文本已经基本是中文（中文字符占比 > 30%），不翻译
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    if chinese_chars / max(len(text), 1) > 0.3:
        return text

    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-v4-flash",
                "messages": [
                    {
                        "role": "system",
                        "content": "你是专业的金融翻译专家。将用户给出的英文金融分析内容准确翻译为简体中文。要求：1) 保持专业术语准确（如hawkish→鹰派，bullish→看涨）；2) 股票代码、数字、百分比保持原样；3) 只输出译文，不要任何解释或注释。"
                    },
                    {"role": "user", "content": f"翻译以下内容为中文：\n\n{text}"}
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=60
        )
        if resp.status_code == 200:
            translated = resp.json()["choices"][0]["message"]["content"].strip()
            _translation_cache[cache_key] = translated
            return translated
        else:
            print(f"⚠️ 翻译失败 {resp.status_code}: {resp.text[:200]}")
            return text
    except Exception as e:
        print(f"⚠️ 翻译异常: {e}")
        return text


# ── 文本处理：提取核心理由 ──────────────────────────────
def extract_key_reasons(text: str, max_points: int = 4) -> list[str]:
    """从一段推理文本里抽出几条关键理由"""
    if not text:
        return []

    lines = re.split(r'\n\s*[-*•]\s+|\n\s*\d+[\.、)]\s+|\n{2,}', text)
    points = [l.strip() for l in lines if l.strip() and len(l.strip()) > 15]

    key_words = ["因为", "由于", "原因", "because", "since", "due to", "表明", "显示",
                 "indicates", "suggests", "shows", "反映", "说明"]
    priority = []
    normal = []
    for p in points:
        if any(kw in p.lower() for kw in key_words):
            priority.append(p)
        else:
            normal.append(p)

    result = (priority + normal)[:max_points]
    return [p[:250] + ("..." if len(p) > 250 else "") for p in result]


def action_label(action: str) -> tuple[str, str]:
    """返回 (emoji+文字, 颜色)"""
    m = {
        "BUY":     ("🟢 买入", "green"),
        "SELL":    ("🔴 卖出", "red"),
        "HOLD":    ("🟡 持有", "yellow"),
        "UNKNOWN": ("⚪ 未定", "grey"),
    }
    return m.get(action, m["UNKNOWN"])


# ── 格式化单标的消息（Telegram）──────────────────────────
def format_ticker_message(summary: dict, analysis_date: str, group: str) -> str:
    ticker = summary["ticker"]
    action = summary["action"]
    action_text, _ = action_label(action)

    lines = [
        f"📈 *{ticker}* | {analysis_date}",
        f"组别: _{group}_",
        "",
        f"决策: *{action_text}*",
        "",
    ]

    # 核心理由（翻译）
    if summary.get("final_decision"):
        print(f"  🔤 翻译 {ticker} 核心理由...")
        reasons_raw = extract_key_reasons(summary["final_decision"], max_points=4)
        if reasons_raw:
            # 合并后一起翻译，减少 API 调用
            joined = "\n---\n".join(reasons_raw)
            translated = translate_to_chinese(joined, max_len=1200)
            reasons_cn = translated.split("\n---\n") if "---" in translated else [translated]

            lines.append("🎯 *核心理由*:")
            for i, r in enumerate(reasons_cn[:4], 1):
                r_safe = r.strip().replace('*', '').replace('_', '').replace('[', '').replace(']', '')
                lines.append(f"{i}. {r_safe}")
            lines.append("")

    # 多空辩论
    if summary.get("investment_plan"):
        print(f"  🔤 翻译 {ticker} 多空辩论...")
        plan_cn = translate_to_chinese(summary["investment_plan"][:500], max_len=500)
        excerpt = plan_cn.replace('*', '').replace('_', '')
        if excerpt:
            lines.append("⚔️ *多空辩论要点*:")
            lines.append(excerpt)
            lines.append("")

    # 交易计划
    if summary.get("trader_plan"):
        print(f"  🔤 翻译 {ticker} 交易计划...")
        trader_cn = translate_to_chinese(summary["trader_plan"][:400], max_len=400)
        trader_excerpt = trader_cn.replace('*', '').replace('_', '')
        if trader_excerpt:
            lines.append("📋 *交易计划*:")
            lines.append(trader_excerpt)
            lines.append("")

    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)


# ── 生成最终裁决汇总表 ──────────────────────────────────
def build_summary_table(summaries: list[dict], analysis_date: str, group: str) -> str:
    """生成最终裁决汇总的 Markdown 文本"""
    lines = [
        f"📊 *{group} 最终裁决汇总*",
        f"📅 {analysis_date}",
        "",
        "```",
        f"{'标的':<8} {'决策':<8}",
        "─" * 20,
    ]
    for s in summaries:
        if not s["success"]:
            lines.append(f"{s['ticker']:<8} ❌ 失败")
        else:
            action_text, _ = action_label(s["action"])
            lines.append(f"{s['ticker']:<8} {action_text}")
    lines.append("```")
    lines.append("")
    lines.append("_详细分析见下方各标的卡片_")
    return "\n".join(lines)


def build_feishu_summary_card(summaries: list[dict], analysis_date: str, group: str) -> dict:
    """生成飞书汇总卡片"""
    rows = []
    for s in summaries:
        if not s["success"]:
            rows.append(f"| **{s['ticker']}** | ❌ 失败 |")
        else:
            action_text, _ = action_label(s["action"])
            rows.append(f"| **{s['ticker']}** | {action_text} |")

    content = "| 标的 | 决策 |\n|------|------|\n" + "\n".join(rows)

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📊 {group} 最终裁决汇总 · {analysis_date}"
                },
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": "_详细分析见下方各标的卡片_"}}
            ]
        }
    }


# ── 格式化飞书单标的卡片 ──────────────────────────────
def format_feishu_card(summary: dict, analysis_date: str, group: str) -> dict:
    ticker = summary["ticker"]
    action = summary["action"]
    action_text, color = action_label(action)

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**决策**: {action_text}\n**组别**: {group}\n**日期**: {analysis_date}"
            }
        },
        {"tag": "hr"},
    ]

    # 核心理由（翻译）
    if summary.get("final_decision"):
        reasons_raw = extract_key_reasons(summary["final_decision"], max_points=4)
        if reasons_raw:
            joined = "\n---\n".join(reasons_raw)
            translated = translate_to_chinese(joined, max_len=1200)
            reasons_cn = translated.split("\n---\n") if "---" in translated else [translated]

            content = "**🎯 核心理由**\n" + "\n".join(f"{i}. {r.strip()}" for i, r in enumerate(reasons_cn[:4], 1))
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content[:1500]}})
            elements.append({"tag": "hr"})

    # 多空辩论
    if summary.get("investment_plan"):
        plan_cn = translate_to_chinese(summary["investment_plan"][:500], max_len=500)
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**⚔️ 多空辩论**\n{plan_cn}"}
        })
        elements.append({"tag": "hr"})

    # 交易计划
    if summary.get("trader_plan"):
        trader_cn = translate_to_chinese(summary["trader_plan"][:400], max_len=400)
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**📋 交易计划**\n{trader_cn}"}
        })

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📈 {ticker} · {analysis_date}"},
                "template": color
            },
            "elements": elements
        }
    }


# ── 飞书签名生成 ──────────────────────────────────────
def gen_feishu_sign(secret: str, timestamp: int) -> str:
    """生成飞书 webhook 签名"""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_feishu_payload(webhook_url: str, payload: dict, secret: str = None) -> requests.Response:
    """发送飞书消息，可选签名"""
    if secret:
        timestamp = int(time.time())
        sign = gen_feishu_sign(secret, timestamp)
        payload = {**payload, "timestamp": str(timestamp), "sign": sign}
    return requests.post(webhook_url, json=payload, timeout=30)


# ── Telegram 推送 ─────────────────────────────────────────
def send_telegram(summaries: list[dict], analysis_date: str, group: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("⚠️  未配置 Telegram，跳过")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def _send(text: str, ticker: str = ""):
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }, timeout=30)
            if resp.status_code == 200:
                print(f"✅ Telegram {ticker or 'header'} 发送成功")
            else:
                # Markdown 解析失败 → 降级纯文本
                resp = requests.post(url, json={
                    "chat_id": chat_id,
                    "text": text.replace('*', '').replace('_', ''),
                }, timeout=30)
                if resp.status_code == 200:
                    print(f"✅ Telegram {ticker or 'header'} 纯文本发送成功")
                else:
                    print(f"❌ Telegram {ticker} 发送失败: {resp.text[:200]}")
        except Exception as e:
            print(f"❌ Telegram {ticker} 发送异常: {e}")

    # 1. 发送最终裁决汇总
    _send(build_summary_table(summaries, analysis_date, group), "汇总")

    # 2. 每标的独立卡片
    for s in summaries:
        if not s["success"]:
            msg = f"📈 *{s['ticker']}* | {analysis_date}\n❌ 分析失败"
        else:
            msg = format_ticker_message(s, analysis_date, group)

        if len(msg) > 4000:
            msg = msg[:4000] + "\n_...(过长已截断)_"

        _send(msg, s["ticker"])


# ── 飞书推送 ──────────────────────────────────────────────
def send_feishu(summaries: list[dict], analysis_date: str, group: str):
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL")
    secret = os.getenv("FEISHU_SECRET")  # 可选

    if not webhook_url:
        print("⚠️  未配置飞书 Webhook，跳过")
        return

    if secret:
        print("🔐 飞书启用签名校验")

    # 1. 发送汇总卡片
    try:
        summary_card = build_feishu_summary_card(summaries, analysis_date, group)
        resp = send_feishu_payload(webhook_url, summary_card, secret)
        if resp.status_code == 200 and resp.json().get("code") == 0:
            print("✅ 飞书 汇总 发送成功")
        else:
            print(f"❌ 飞书 汇总 发送失败: {resp.text[:300]}")
    except Exception as e:
        print(f"❌ 飞书汇总异常: {e}")

    # 2. 每标的卡片
    for s in summaries:
        try:
            if not s["success"]:
                payload = {
                    "msg_type": "text",
                    "content": {"text": f"❌ {s['ticker']} 分析失败: {s['final_decision'][:200]}"}
                }
            else:
                payload = format_feishu_card(s, analysis_date, group)

            resp = send_feishu_payload(webhook_url, payload, secret)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                print(f"✅ 飞书 {s['ticker']} 发送成功")
            else:
                print(f"❌ 飞书 {s['ticker']} 发送失败: {resp.text[:300]}")
        except Exception as e:
            print(f"❌ 飞书 {s['ticker']} 发送异常: {e}")


# ── 主函数 ────────────────────────────────────────────────
def main():
    json_path = os.getenv("JSON_PATH")
    report_path = os.getenv("REPORT_PATH")
    analysis_date = os.getenv("ANALYSIS_DATE", "未知日期")
    tickers = os.getenv("TICKERS", "")
    group = os.getenv("REPORT_GROUP", "TradingAgents")

    print(f"REPORT_PATH: {report_path}")
    print(f"JSON_PATH: {json_path}")
    print(f"ANALYSIS_DATE: {analysis_date}")
    print(f"GROUP: {group}")

    summaries = []
    if json_path and Path(json_path).exists():
        summaries = json.loads(Path(json_path).read_text(encoding="utf-8"))
        print(f"📄 从 JSON 读取 {len(summaries)} 个标的")
    else:
        reports_dir = Path(__file__).parent.parent / "reports"
        if reports_dir.exists():
            json_files = list(reports_dir.glob("summary_*.json"))
            if json_files:
                latest = max(json_files, key=lambda p: p.stat().st_mtime)
                summaries = json.loads(latest.read_text(encoding="utf-8"))
                print(f"📄 自动发现 JSON: {latest}")

    if not summaries:
        print("⚠️ 无可用的结构化摘要，发送失败通知")
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if token and chat_id:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": f"⚠️ {group} 报告生成失败\n日期: {analysis_date}\n标的: {tickers}"
                },
                timeout=30
            )
        return

    send_telegram(summaries, analysis_date, group)
    send_feishu(summaries, analysis_date, group)

    print("✅ 推送完成")


if __name__ == "__main__":
    main()
