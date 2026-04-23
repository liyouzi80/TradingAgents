"""
TradingAgents 自动化报告生成脚本
每日定时运行，读取 tickers.txt，生成分析报告并推送到 Telegram/飞书
"""

import os
import sys
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

# ── 路径设置 ──────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
TICKERS_FILE = ROOT / "tickers.txt"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# ── 读取标的 ──────────────────────────────────────────────
def load_tickers() -> list[str]:
    if not TICKERS_FILE.exists():
        print("❌ tickers.txt 不存在，请创建后重试")
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


# ── 获取分析日期（跳过周末）────────────────────────────────
def get_analysis_date() -> str:
    date = datetime.now()
    # 如果今天是周一，分析上周五的数据
    if date.weekday() == 0:
        date -= timedelta(days=3)
    # 如果今天是周日，分析上周五
    elif date.weekday() == 6:
        date -= timedelta(days=2)
    # 如果今天是周六，分析上周五
    elif date.weekday() == 5:
        date -= timedelta(days=1)
    return date.strftime("%Y-%m-%d")


# ── 运行 TradingAgents 分析 ───────────────────────────────
def run_analysis(tickers: list[str], analysis_date: str) -> str:
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()

    # LLM 配置 —— 优先读环境变量，方便 GitHub Secrets 注入
    llm_provider = os.getenv("LLM_PROVIDER", "deepseek")
    config["llm_provider"] = llm_provider

    if llm_provider == "deepseek":
        config["deep_think_llm"] = os.getenv("DEEP_THINK_MODEL", "deepseek-chat")
        config["quick_think_llm"] = os.getenv("QUICK_THINK_MODEL", "deepseek-chat")
    elif llm_provider == "google":
        config["deep_think_llm"] = os.getenv("DEEP_THINK_MODEL", "gemini-2.0-flash")
        config["quick_think_llm"] = os.getenv("QUICK_THINK_MODEL", "gemini-2.0-flash")
    elif llm_provider == "openai":
        config["deep_think_llm"] = os.getenv("DEEP_THINK_MODEL", "gpt-4o")
        config["quick_think_llm"] = os.getenv("QUICK_THINK_MODEL", "gpt-4o-mini")

    config["max_debate_rounds"] = int(os.getenv("MAX_DEBATE_ROUNDS", "1"))
    config["online_tools"] = True

    # 逐个标的分析，汇总结果
    all_results = {}
    for ticker in tickers:
        print(f"\n🔍 正在分析 {ticker} ({analysis_date})...")
        try:
            ta = TradingAgentsGraph(
                selected_analysts=["market", "social", "news", "fundamentals"],
                config=config,
                debug=False,
            )
            state, decision = ta.propagate(ticker, analysis_date)
            all_results[ticker] = {
                "decision": decision,
                "state": state,
            }
            print(f"✅ {ticker} 分析完成")
        except Exception as e:
            print(f"❌ {ticker} 分析失败: {e}")
            all_results[ticker] = {"decision": f"分析失败: {e}", "state": {}}

    # 生成报告
    report = build_report(all_results, tickers, analysis_date)
    return report


# ── 构建 Markdown 报告 ────────────────────────────────────
def build_report(results: dict, tickers: list[str], analysis_date: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ticker_str = ", ".join(tickers)

    lines = [
        f"# Trading Analysis Report: {ticker_str}",
        f"\nGenerated: {now}  |  Analysis Date: {analysis_date}\n",
        "---\n",
        "## 📊 最终裁决汇总\n",
        "| 标的 | 决策 | 摘要 |",
        "|------|------|------|",
    ]

    for ticker, data in results.items():
        decision = data.get("decision", "N/A")
        # 提取关键信息
        if isinstance(decision, dict):
            action = decision.get("action", "N/A")
            summary = decision.get("reasoning", "")[:100] + "..."
        else:
            action = str(decision)[:50]
            summary = ""
        lines.append(f"| **{ticker}** | {action} | {summary} |")

    lines.append("\n---\n")
    lines.append("## 📝 详细分析\n")

    for ticker, data in results.items():
        lines.append(f"### {ticker}\n")
        decision = data.get("decision", "N/A")
        if isinstance(decision, dict):
            lines.append(f"**决策**: {decision.get('action', 'N/A')}\n")
            lines.append(f"**理由**: {decision.get('reasoning', 'N/A')}\n")
        else:
            lines.append(f"{decision}\n")
        lines.append("")

    return "\n".join(lines)


# ── 保存报告到文件 ────────────────────────────────────────
def save_report(report: str, analysis_date: str) -> Path:
    filename = REPORTS_DIR / f"report_{analysis_date}.md"
    filename.write_text(report, encoding="utf-8")
    print(f"💾 报告已保存: {filename}")
    return filename


# ── 主函数 ────────────────────────────────────────────────
def main():
    print("🚀 TradingAgents 自动报告启动")
    tickers = load_tickers()
    analysis_date = os.getenv("ANALYSIS_DATE") or get_analysis_date()
    print(f"📅 分析日期: {analysis_date}")

    report = run_analysis(tickers, analysis_date)
    report_path = save_report(report, analysis_date)

    # 将报告路径写入环境文件，供后续步骤读取
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"report_path={report_path}\n")
            f.write(f"analysis_date={analysis_date}\n")
            f.write(f"tickers={','.join(tickers)}\n")

    print("\n✅ 报告生成完成")
    print(report[:500] + "...")  # 预览前500字


if __name__ == "__main__":
    main()
