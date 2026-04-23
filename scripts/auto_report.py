"""
TradingAgents 自动化报告生成脚本
直接调用 Python API，绕过 CLI 交互式输入
"""

import os
import sys
from datetime import datetime, timedelta
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
    date = datetime.now()
    if date.weekday() == 0:
        date -= timedelta(days=3)
    elif date.weekday() == 6:
        date -= timedelta(days=2)
    elif date.weekday() == 5:
        date -= timedelta(days=1)
    return date.strftime("%Y-%m-%d")


def build_config() -> dict:
    llm_provider = os.getenv("LLM_PROVIDER", "deepseek")
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
    return {
        "llm_provider": llm_provider,
        "backend_url": os.getenv("BACKEND_URL", "https://api.deepseek.com/v1"),
        "deep_think_llm": models["deep"],
        "quick_think_llm": models["quick"],
        "max_debate_rounds": int(os.getenv("MAX_DEBATE_ROUNDS", "1")),
        "max_risk_discuss_rounds": int(os.getenv("MAX_RISK_ROUNDS", "1")),
        "online_tools": True,
    }


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


def build_report(results: list[dict], analysis_date: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tickers_str = ", ".join(r["ticker"] for r in results)
    lines = [
        f"# Trading Analysis Report",
        f"\n**标的**: {tickers_str}",
        f"**生成时间**: {now}",
        f"**分析日期**: {analysis_date}\n",
        "---\n",
        "## 📊 最终裁决汇总\n",
        "| 标的 | 状态 | 决策 |",
        "|------|------|------|",
    ]
    for r in results:
        status = "✅" if r["success"] else "❌"
        decision = r["decision"]
        action = decision.get("action", str(decision)[:100]) if isinstance(decision, dict) else str(decision)[:100]
        lines.append(f"| **{r['ticker']}** | {status} | {action} |")
    lines.append("\n---\n## 📝 详细分析\n")
    for r in results:
        lines.append(f"### {r['ticker']}\n")
        decision = r["decision"]
        if isinstance(decision, dict):
            for k, v in decision.items():
                lines.append(f"**{k}**: {v}\n")
        else:
            lines.append(f"{decision}\n")
        state = r.get("state", {})
        if state and isinstance(state, dict):
            for key in ["final_trade_decision", "investment_plan", "risk_assessment"]:
                if key in state and state[key]:
                    lines.append(f"\n**{key}**:\n{state[key]}\n")
        lines.append("")
    return "\n".join(lines)


def save_report(report: str, analysis_date: str) -> Path:
    filename = REPORTS_DIR / f"report_{analysis_date}.md"
    filename.write_text(report, encoding="utf-8")
    print(f"\n💾 报告已保存: {filename}")
    return filename


def write_github_output(report_path: Path, analysis_date: str, tickers: list[str]):
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"report_path={report_path}\n")
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
    report = build_report(results, analysis_date)
    report_path = save_report(report, analysis_date)
    write_github_output(report_path, analysis_date, tickers)
    print("\n" + "="*50)
    print(report[:800])
    print("\n✅ 所有分析完成")


if __name__ == "__main__":
    main()
