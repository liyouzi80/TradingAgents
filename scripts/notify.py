"""
推送报告到 Telegram 和飞书
按标的分条发送，每个标的独立一条消息，包含决策 + 理由 + 分歧
"""

import os
import sys
import json
import re
import requests
from pathlib import Path


# ── 文本处理：提取核心理由 ──────────────────────────────
def extract_key_reasons(text: str, max_points: int = 4) -> list[str]:
    """从一段推理文本里抽出几条关键理由"""
    if not text:
        return []

    # 先按段落/列表项切分
    lines = re.split(r'\n\s*[-*•]\s+|\n\s*\d+[\.、)]\s+|\n{2,}', text)
    points = [l.strip() for l in lines if l.strip() and len(l.strip()) > 15]

    # 优先取看起来像"论点"的句子（包含因为、由于、原因、since、because、due to）
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
    # 每条截断到合理长度
    return [p[:200] + ("..." if len(p) > 200 else "") for p in result]


def format_ticker_message(summary: dict, analysis_date: str, group: str) -> str:
    """格式化单个标的的 Telegram Markdown 消息"""
    ticker = summary["ticker"]
    action = summary["action"]

    action_emoji = {
        "BUY": "🟢 *买入*",
        "SELL": "🔴 *卖出*",
        "HOLD": "🟡 *持有*",
        "UNKNOWN": "⚪ *未定*"
    }.get(action, "⚪ *未定*")

    lines = [
        f"📈 *{ticker}* | {analysis_date}",
        f"组别: _{group}_",
        "",
        f"{action_emoji}",
        "",
    ]

    # 核心理由（从 final_decision 抽取）
    if summary.get("final_decision"):
        reasons = extract_key_reasons(summary["final_decision"], max_points=4)
        if reasons:
            lines.append("🎯 *核心理由*:")
            for i, r in enumerate(reasons, 1):
                # Markdown 转义
                r_safe = r.replace('*', '').replace('_', '').replace('[', '').replace(']', '')
                lines.append(f"{i}. {r_safe}")
            lines.append("")

    # 多空分歧（从 investment_plan 抽取）
    if summary.get("investment_plan"):
        plan_text = summary["investment_plan"]
        # 只取前 400 字作为分歧摘要
        excerpt = plan_text[:400].replace('*', '').replace('_', '')
        if excerpt:
            lines.append("⚔️ *多空辩论要点*:")
            lines.append(excerpt.replace('\n\n', '\n'))
            if len(plan_text) > 400:
                lines.append("_(更多内容见完整报告)_")
            lines.append("")

    # 交易计划
    if summary.get("trader_plan"):
        trader_excerpt = summary["trader_plan"][:300].replace('*', '').replace('_', '')
        if trader_excerpt:
            lines.append("📋 *交易计划*:")
            lines.append(trader_excerpt)
            lines.append("")

    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_feishu_card(summary: dict, analysis_date: str, group: str) -> dict:
    """格式化飞书交互式卡片"""
    ticker = summary["ticker"]
    action = summary["action"]

    color_map = {"BUY": "green", "SELL": "red", "HOLD": "yellow", "UNKNOWN": "grey"}
    action_text = {"BUY": "🟢 买入", "SELL": "🔴 卖出", "HOLD": "🟡 持有", "UNKNOWN": "⚪ 未定"}

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**决策**: {action_text.get(action, '⚪ 未定')}\n**组别**: {group}\n**日期**: {analysis_date}"
            }
        },
        {"tag": "hr"},
    ]

    # 核心理由
    if summary.get("final_decision"):
        reasons = extract_key_reasons(summary["final_decision"], max_points=4)
        if reasons:
            content = "**🎯 核心理由**\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(reasons, 1))
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content[:1500]}})
            elements.append({"tag": "hr"})

    # 多空辩论
    if summary.get("investment_plan"):
        excerpt = summary["investment_plan"][:500]
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"**⚔️ 多空辩论**\n{excerpt}"}})
        elements.append({"tag": "hr"})

    # 交易计划
    if summary.get("trader_plan"):
        excerpt = summary["trader_plan"][:400]
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"**📋 交易计划**\n{excerpt}"}})

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📈 {ticker} · {analysis_date}"
                },
                "template": color_map.get(action, "blue")
            },
            "elements": elements
        }
    }


# ── Telegram 推送 ─────────────────────────────────────────
def send_telegram(summaries: list[dict], analysis_date: str, group: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("⚠️  未配置 Telegram，跳过")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # 先发送头部汇总
    header = f"🔔 *{group} 每日报告*\n📅 {analysis_date}\n标的: {', '.join(s['ticker'] for s in summaries)}\n"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": header,
        "parse_mode": "Markdown",
    }, timeout=30)

    # 每个标的一条消息
    for s in summaries:
        if not s["success"]:
            msg = f"📈 *{s['ticker']}* | {analysis_date}\n❌ 分析失败"
        else:
            msg = format_ticker_message(s, analysis_date, group)

        # Telegram 单条上限 4096 字符
        if len(msg) > 4000:
            msg = msg[:4000] + "\n_...(过长已截断)_"

        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }, timeout=30)
            if resp.status_code == 200:
                print(f"✅ Telegram {s['ticker']} 发送成功")
            else:
                # Markdown 解析失败时降级为纯文本
                print(f"⚠️ Markdown 失败，降级为纯文本: {resp.text[:200]}")
                resp = requests.post(url, json={
                    "chat_id": chat_id,
                    "text": msg.replace('*', '').replace('_', ''),
                }, timeout=30)
                if resp.status_code == 200:
                    print(f"✅ Telegram {s['ticker']} 纯文本发送成功")
        except Exception as e:
            print(f"❌ Telegram {s['ticker']} 发送异常: {e}")


# ── 飞书 Webhook 推送 ─────────────────────────────────────
def send_feishu(summaries: list[dict], analysis_date: str, group: str):
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL")

    if not webhook_url:
        print("⚠️  未配置飞书 Webhook，跳过")
        return

    # 先发送头部
    header = {
        "msg_type": "text",
        "content": {
            "text": f"🔔 {group} 每日报告 · {analysis_date}\n标的: {', '.join(s['ticker'] for s in summaries)}"
        }
    }
    requests.post(webhook_url, json=header, timeout=30)

    # 每个标的一张卡片
    for s in summaries:
        try:
            if not s["success"]:
                payload = {
                    "msg_type": "text",
                    "content": {"text": f"❌ {s['ticker']} 分析失败: {s['final_decision'][:200]}"}
                }
            else:
                payload = format_feishu_card(s, analysis_date, group)

            resp = requests.post(webhook_url, json=payload, timeout=30)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                print(f"✅ 飞书 {s['ticker']} 发送成功")
            else:
                print(f"❌ 飞书 {s['ticker']} 发送失败: {resp.text[:200]}")
        except Exception as e:
            print(f"❌ 飞书 {s['ticker']} 发送异常: {e}")


# ── 主函数 ────────────────────────────────────────────────
def main():
    # 优先读 JSON（结构化数据），fallback 到 md
    json_path = os.getenv("JSON_PATH")
    report_path = os.getenv("REPORT_PATH")
    analysis_date = os.getenv("ANALYSIS_DATE", "未知日期")
    tickers = os.getenv("TICKERS", "")
    group = os.getenv("REPORT_GROUP", "TradingAgents")

    print(f"REPORT_PATH: {report_path}")
    print(f"JSON_PATH: {json_path}")
    print(f"ANALYSIS_DATE: {analysis_date}")
    print(f"GROUP: {group}")

    # 从 JSON 读取结构化摘要
    summaries = []
    if json_path and Path(json_path).exists():
        summaries = json.loads(Path(json_path).read_text(encoding="utf-8"))
        print(f"📄 从 JSON 读取 {len(summaries)} 个标的")
    else:
        # 尝试从 reports 目录自动发现 JSON
        reports_dir = Path(__file__).parent.parent / "reports"
        if reports_dir.exists():
            json_files = list(reports_dir.glob("summary_*.json"))
            if json_files:
                latest = max(json_files, key=lambda p: p.stat().st_mtime)
                summaries = json.loads(latest.read_text(encoding="utf-8"))
                print(f"📄 自动发现 JSON: {latest}")

    if not summaries:
        # 兜底：发送失败通知
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
