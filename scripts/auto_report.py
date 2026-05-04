"""
TradingAgents 自动化报告生成脚本
提取结构化字段，生成中等详细度报告
"""

import os
import sys
import copy
import json
import requests
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
TICKERS_FILE = ROOT / "tickers.txt"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def load_tickers() -> list[str]:
    if not TICKERS_FILE.exists():
        print("❌ tickers.txt 不存在")
        sys.exit(1)
    tickers = [
        line.strip().upper()
        for line in TICKERS_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not tickers:
        print("❌ tickers.txt 为空")
        sys.exit(1)
    print(f"📋 读取到标的: {', '.join(tickers)}")
    return tickers


def get_analysis_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def build_config() -> dict:
    from tradingagents.default_config import DEFAULT_CONFIG

    config = copy.deepcopy(DEFAULT_CONFIG)
    llm_provider = os.getenv("LLM_PROVIDER", "deepseek")
    config["llm_provider"] = llm_provider

    model_map = {
        "deepseek": {
            "deep": os.getenv("DEEP_THINK_MODEL", "deepseek-chat"),
            "quick": os.getenv("QUICK_THINK_MODEL", "deepseek-chat"),
        },
        "google": {
            "deep": os.getenv("DEEP_THINK_MODEL", "gemini-2.0-flash"),
            "quick": os.getenv("QUICK_THINK_MODEL", "gemini-2.0-flash"),
        },
        "openai": {
            "deep": os.getenv("DEEP_THINK_MODEL", "gpt-4o"),
            "quick": os.getenv("QUICK_THINK_MODEL", "gpt-4o-mini"),
        },
    }
    models = model_map.get(llm_provider, model_map["deepseek"])
    config["deep_think_llm"] = models["deep"]
    config["quick_think_llm"] = models["quick"]

    # 调试:打印实际使用的模型名(脱敏显示)
    deep_model = models["deep"]
    quick_model = models["quick"]
    print(f"🔍 DEBUG: deep_model = {deep_model[:6]}...{deep_model[-6:] if len(deep_model) > 12 else ''}, len={len(deep_model)}")
    print(f"🔍 DEBUG: quick_model = {quick_model[:6]}...{quick_model[-6:] if len(quick_model) > 12 else ''}, len={len(quick_model)}")
    config["max_debate_rounds"] = int(os.getenv("MAX_DEBATE_ROUNDS", "1"))
    config["max_risk_discuss_rounds"] = int(os.getenv("MAX_RISK_ROUNDS", "1"))
    config["online_tools"] = True

    return config


def analyze_ticker(ticker: str, analysis_date: str, config: dict) -> dict:
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    print(f"\n{'='*50}\n🔍 分析 {ticker} | 日期: {analysis_date}\n{'='*50}")
    try:
        ta = TradingAgentsGraph(
            selected_analysts=["market", "social", "news", "fundamentals"],
            config=config,
            debug=False,
        )
        state, decision = ta.propagate(ticker, analysis_date)
        print(f"✅ {ticker} 分析完成")
        return {"ticker": ticker, "success": True, "decision": decision, "state": state}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ticker": ticker, "success": False, "decision": str(e), "state": {}}


def translate_summary_fields(summaries: list[dict]):
    """将每个标的的核心理由/多空辩论/交易计划翻译为中文，写入 _cn 字段"""
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return

    for s in summaries:
        for field in ["final_decision", "investment_plan", "trader_plan"]:
            text = s.get(field, "")
            if not text or not text.strip():
                continue
            # 已有中文则跳过
            chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
            if chinese_chars / max(len(text), 1) > 0.3:
                s[f"{field}_cn"] = text
                continue
            try:
                resp = requests.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": "deepseek-v4-flash",
                        "messages": [
                            {"role": "system", "content": "你是专业的金融翻译专家。将英文金融分析内容准确翻译为简体中文。要求：1) 专业术语准确（Underweight→减持, MACD histogram→MACD柱状图, Bollinger Bands→布林带, stop loss→止损, position sizing→仓位管理, hawkish→鹰派, bullish→看涨）；2) 股票代码、数字、百分比保持原样；3) 保持原始 Markdown 格式（**, 列表等）；4) 只输出译文，不要解释。"},
                            {"role": "user", "content": f"翻译以下金融分析为中文：\n\n{text}"},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 4096,
                    },
                    timeout=120,
                )
                if resp.status_code == 200:
                    s[f"{field}_cn"] = resp.json()["choices"][0]["message"]["content"].strip()
                else:
                    print(f"  ⚠️ 翻译 {s['ticker']} {field} 失败: {resp.status_code}")
            except Exception as e:
                print(f"  ⚠️ 翻译 {s['ticker']} {field} 异常: {e}")


def extract_ticker_summary(result: dict) -> dict:
    """从 state 中提取关键字段，供推送脚本使用"""
    ticker = result["ticker"]
    state = result.get("state", {}) or {}
    decision = result.get("decision", "")

    # 核心字段提取
    summary = {
        "ticker": ticker,
        "success": result["success"],
        "final_decision": "",           # 最终决策（BUY/SELL/HOLD + 理由）
        "investment_plan": "",          # 研究团队辩论结论
        "trader_plan": "",              # 交易员计划
        "market_report": "",            # 技术面
        "sentiment_report": "",         # 情绪面
        "news_report": "",              # 新闻面
        "fundamentals_report": "",      # 基本面
        "raw_decision": str(decision),  # 原始 decision
    }

    if isinstance(state, dict):
        summary["final_decision"] = state.get("final_trade_decision", "") or str(decision)
        summary["investment_plan"] = state.get("investment_plan", "")
        summary["trader_plan"] = state.get("trader_investment_plan", "")
        summary["market_report"] = state.get("market_report", "")
        summary["sentiment_report"] = state.get("sentiment_report", "")
        summary["news_report"] = state.get("news_report", "")
        summary["fundamentals_report"] = state.get("fundamentals_report", "")

    return summary


def extract_action(text: str) -> str:
    """从决策文本里提取 BUY/SELL/HOLD"""
    if not text:
        return "UNKNOWN"
    text_upper = text.upper()
    # 优先匹配明确的关键词
    for keyword in ["**BUY**", "**SELL**", "**HOLD**", "FINAL DECISION: BUY",
                    "FINAL DECISION: SELL", "FINAL DECISION: HOLD"]:
        if keyword in text_upper:
            return keyword.replace("*", "").replace("FINAL DECISION: ", "").strip()
    # 宽松匹配
    if "BUY" in text_upper[:500]:
        return "BUY"
    if "SELL" in text_upper[:500]:
        return "SELL"
    if "HOLD" in text_upper[:500]:
        return "HOLD"
    return "UNKNOWN"


def build_report(results: list[dict], analysis_date: str) -> tuple[str, list[dict]]:
    """
    返回 (markdown_report, structured_summaries)
    structured_summaries 供推送脚本使用
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tickers_str = ", ".join(r["ticker"] for r in results)

    # 提取所有标的的结构化摘要
    summaries = [extract_ticker_summary(r) for r in results]

    # 为每个摘要补充 action
    for s in summaries:
        s["action"] = extract_action(s["final_decision"])

    # 翻译为中文（如有 DEEPSEEK_API_KEY）
    translate_summary_fields(summaries)

    # 生成 markdown
    lines = [
        f"# Trading Analysis Report",
        f"\n**标的**: {tickers_str}",
        f"**生成时间**: {now}",
        f"**分析日期**: {analysis_date}\n",
        "---\n",
        "## 📊 最终裁决汇总\n",
        "| 标的 | 决策 | 摘要 |",
        "|------|------|------|",
    ]

    for s in summaries:
        if not s["success"]:
            lines.append(f"| **{s['ticker']}** | ❌ 失败 | {s['final_decision'][:80]} |")
            continue

        action = s["action"]
        action_emoji = {"BUY": "🟢 买入", "SELL": "🔴 卖出", "HOLD": "🟡 持有"}.get(action, "⚪ 未知")
        brief = s["final_decision"][:100].replace("\n", " ").replace("|", "｜")
        lines.append(f"| **{s['ticker']}** | {action_emoji} | {brief}... |")

    lines.append("\n---\n## 📝 详细分析\n")

    for s in summaries:
        ticker = s["ticker"]
        lines.append(f"\n## {ticker}\n")

        if not s["success"]:
            lines.append(f"❌ **分析失败**: {s['final_decision']}\n")
            continue

        action = s["action"]
        action_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(action, "⚪")
        lines.append(f"### {action_emoji} 最终决策: **{action}**\n")

        if s["final_decision"]:
            lines.append(f"**裁决理由**:\n{s['final_decision']}\n")

        if s["investment_plan"]:
            lines.append(f"\n### 🔬 研究团队结论\n{s['investment_plan']}\n")

        if s["trader_plan"]:
            lines.append(f"\n### 📈 交易员计划\n{s['trader_plan']}\n")

        # 基本面 / 技术面 / 新闻 / 情绪
        if s["fundamentals_report"]:
            lines.append(f"\n<details><summary>📊 基本面分析</summary>\n\n{s['fundamentals_report']}\n\n</details>\n")
        if s["market_report"]:
            lines.append(f"\n<details><summary>📉 技术面分析</summary>\n\n{s['market_report']}\n\n</details>\n")
        if s["news_report"]:
            lines.append(f"\n<details><summary>📰 新闻面分析</summary>\n\n{s['news_report']}\n\n</details>\n")
        if s["sentiment_report"]:
            lines.append(f"\n<details><summary>💬 情绪面分析</summary>\n\n{s['sentiment_report']}\n\n</details>\n")

        lines.append("\n---\n")

    return "\n".join(lines), summaries


def save_report(report: str, summaries: list[dict], analysis_date: str) -> tuple[Path, Path]:
    """保存 markdown 报告 + JSON 结构化摘要(文件名带分组,避免并发冲突)"""
    # 从环境变量取分组名,转成文件名安全的格式
    group_raw = os.getenv("REPORT_GROUP", "manual")
    group_slug = group_raw.lower().replace(" ", "-").replace("/", "-")

    md_path = REPORTS_DIR / f"report_{analysis_date}_{group_slug}.md"
    md_path.write_text(report, encoding="utf-8")
    print(f"\n💾 MD 报告已保存: {md_path}")

    json_path = REPORTS_DIR / f"summary_{analysis_date}_{group_slug}.json"
    json_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 JSON 摘要已保存: {json_path}")

    return md_path, json_path


def write_github_output(report_path: Path, json_path: Path, analysis_date: str, tickers: list[str]):
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"report_path={report_path}\n")
            f.write(f"json_path={json_path}\n")
            f.write(f"analysis_date={analysis_date}\n")
            f.write(f"tickers={','.join(tickers)}\n")
        print("✅ GitHub Output 已写入")


def main():
    print("🚀 TradingAgents 自动报告启动")
    print(f"Python: {sys.version}")
    tickers = load_tickers()
    analysis_date = os.getenv("ANALYSIS_DATE") or get_analysis_date()
    print(f"📅 分析日期: {analysis_date}")
    config = build_config()
    print(f"🤖 LLM: {config['llm_provider']} | deep={config['deep_think_llm']} | quick={config['quick_think_llm']}")
    results = [analyze_ticker(t, analysis_date, config) for t in tickers]
    report, summaries = build_report(results, analysis_date)
    md_path, json_path = save_report(report, summaries, analysis_date)
    write_github_output(md_path, json_path, analysis_date, tickers)
    print("\n" + "="*50)
    print(report[:800])
    print("\n✅ 所有分析完成")


if __name__ == "__main__":
    main()
