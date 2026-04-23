"""
TradingAgents 自动化报告生成脚本
适用于 GitHub Actions / Cron / CI 环境

功能：
- 读取 tickers.txt
- 调用 TradingAgents 分析
- 自动生成 Markdown 报告
- 输出 GitHub Actions 环境变量
- 防止 Agent 卡死
- 提高 CI 可观测性
"""

import os
import sys
import traceback
import signal
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List


# ─────────────────────────────────────────────
# 路径配置
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
TICKERS_FILE = ROOT / "tickers.txt"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────
# 超时异常
# ─────────────────────────────────────────────
class TimeoutException(Exception):
    pass


def timeout_handler(signum, frame):
    raise TimeoutException("Ticker 分析超时")


# ─────────────────────────────────────────────
# 日志输出
# ─────────────────────────────────────────────
def log(message: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


# ─────────────────────────────────────────────
# 读取标的
# ─────────────────────────────────────────────
def load_tickers() -> List[str]:
    if not TICKERS_FILE.exists():
        log("❌ tickers.txt 不存在")
        sys.exit(1)

    tickers = []

    for line in TICKERS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line:
            continue

    main()
