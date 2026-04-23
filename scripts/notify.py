"""
推送报告到 Telegram 和飞书
支持长消息自动分段发送
"""

import os
import sys
import requests
from pathlib import Path


# ── Telegram 推送 ─────────────────────────────────────────
def send_telegram(report: str, analysis_date: str, tickers: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("⚠️  未配置 Telegram，跳过")
        return

    # Telegram 单条消息上限 4096 字符，超出则分段
    header = f"📈 *TradingAgents 每日报告*\n📅 {analysis_date} | 标的: `{tickers}`\n\n"
    max_len = 4000

    # 提取精简摘要发送（避免全文太长）
    summary = extract_summary(report)
    message = header + summary

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # 分段发送
    chunks = [message[i:i+max_len] for i in range(0, len(message), max_len)]
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            print(f"✅ Telegram 发送成功 (段 {i+1}/{len(chunks)})")
        else:
            print(f"❌ Telegram 发送失败: {resp.text}")


# ── 飞书 Webhook 推送 ─────────────────────────────────────
def send_feishu(report: str, analysis_date: str, tickers: str):
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL")

    if not webhook_url:
        print("⚠️  未配置飞书 Webhook，跳过")
        return

    summary = extract_summary(report)

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📈 TradingAgents 每日报告 · {analysis_date}"
                },
                "template": "blue"
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**标的**: {tickers}\n**日期**: {analysis_date}"
                    }
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": summary[:2000]  # 飞书卡片内容限制
                    }
                },
            ]
        }
    }

    resp = requests.post(webhook_url, json=payload, timeout=30)
    if resp.status_code == 200 and resp.json().get("code") == 0:
        print("✅ 飞书发送成功")
    else:
        print(f"❌ 飞书发送失败: {resp.text}")


# ── 提取精简摘要 ──────────────────────────────────────────
def extract_summary(report: str) -> str:
    """从完整报告中提取最终裁决汇总部分"""
    lines = report.splitlines()
    summary_lines = []
    in_summary = False

    for line in lines:
        if "最终裁决汇总" in line or "Final" in line.upper():
            in_summary = True
        if in_summary:
            summary_lines.append(line)
        # 遇到详细分析部分停止
        if in_summary and "详细分析" in line:
            break

    if summary_lines:
        return "\n".join(summary_lines[:50])  # 最多50行

    # 如果没找到汇总部分，返回前1000字符
    return report[:1000] + "\n\n_[完整报告见 GitHub Actions Artifacts]_"


# ── 主函数 ────────────────────────────────────────────────
def main():
    # 从环境变量或命令行参数读取
    report_path = os.getenv("REPORT_PATH") or (sys.argv[1] if len(sys.argv) > 1 else None)
    analysis_date = os.getenv("ANALYSIS_DATE", "未知日期")
    tickers = os.getenv("TICKERS", "未知标的")

    if not report_path or not Path(report_path).exists():
        print(f"❌ 报告文件不存在: {report_path}")
        sys.exit(1)

    report = Path(report_path).read_text(encoding="utf-8")
    print(f"📄 读取报告: {report_path} ({len(report)} 字符)")

    send_telegram(report, analysis_date, tickers)
    send_feishu(report, analysis_date, tickers)

    print("✅ 推送完成")


if __name__ == "__main__":
    main()
