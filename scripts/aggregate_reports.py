#!/usr/bin/env python3
"""
Aggregate TradingAgents summary JSON files into a single dashboard data file.
Usage:
  python3 scripts/aggregate_reports.py [reports_dir] [output_dir]

Defaults:
  reports_dir = reports/
  output_dir  = dashboard/

Output: dashboard_data.json (includes ticker whitelist for filtering)
"""

import json
import glob
import os
import re
import sys


def normalize_action(action, raw_decision):
    if action and action != 'UNKNOWN':
        return action
    rd = (raw_decision or '').lower()
    if any(w in rd for w in ('overweight', 'buy')):
        return 'BUY'
    if any(w in rd for w in ('underweight', 'sell')):
        return 'SELL'
    return 'HOLD'


def parse_ticker_file(fpath):
    tickers = set()
    if not os.path.isfile(fpath):
        return tickers
    with open(fpath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            tickers.add(line.upper())
    return tickers


def read_ticker_whitelist(tickers_dir):
    """Parse all ticker files in tickers_dir plus root tickers.txt, return set of valid symbols."""
    tickers = set()
    # tickers/ directory files
    if os.path.isdir(tickers_dir):
        for fname in os.listdir(tickers_dir):
            fpath = os.path.join(tickers_dir, fname)
            if os.path.isfile(fpath) and not fname.startswith('.'):
                tickers.update(parse_ticker_file(fpath))
    # root tickers.txt
    root_tickers = os.path.join(os.path.dirname(tickers_dir), 'tickers.txt')
    tickers.update(parse_ticker_file(root_tickers))
    return tickers


def aggregate(reports_dir):
    files = sorted(glob.glob(os.path.join(reports_dir, 'summary_*.json')))
    if not files:
        print('No summary_*.json files found.', file=sys.stderr)
        return []

    entries = []
    for filepath in files:
        basename = os.path.basename(filepath)
        match = re.match(r'summary_(\d{4}-\d{2}-\d{2})(?:_(.+))?\.json', basename)
        if not match:
            print(f'Skipping unrecognized file: {basename}', file=sys.stderr)
            continue
        date_str = match.group(1)
        variant = match.group(2) or 'standard'

        with open(filepath) as f:
            data = json.load(f)

        if isinstance(data, dict):
            data = [data]
        for item in data:
            if not item.get('success', True):
                continue
            entries.append({
                'date': date_str,
                'variant': variant,
                'ticker': item.get('ticker', ''),
                'action': normalize_action(item.get('action'), item.get('raw_decision')),
                'raw_decision': item.get('raw_decision', ''),
                'final_decision': item.get('final_decision', ''),
                'investment_plan': item.get('investment_plan', ''),
                'trader_plan': item.get('trader_plan', ''),
            })

    return entries


def get_repo_root():
    """Find repo root relative to this script's location."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    root = get_repo_root()
    reports_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, 'reports')
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, 'dashboard')
    tickers_dir = os.path.join(root, 'tickers')

    entries = aggregate(reports_dir)
    if not entries:
        print('No entries found.', file=sys.stderr)
        sys.exit(1)

    whitelist = read_ticker_whitelist(tickers_dir)

    # Mark entries with whether their ticker is in the whitelist
    visible = sum(1 for e in entries if e['ticker'] in whitelist)
    hidden = sum(1 for e in entries if e['ticker'] not in whitelist)

    output = {
        'entries': entries,
        'visible_tickers': sorted(whitelist),
    }

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, 'dashboard_data.json')
    with open(output_path, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    dates = sorted(set((e['date'], e['variant']) for e in entries))
    print(f'Wrote {len(entries)} entries ({len(dates)} date-variant combos) to {output_path}')
    print(f'Whitelist: {len(whitelist)} tickers loaded from {tickers_dir}')
    print(f'Visible: {visible} entries ({len(set(e["ticker"] for e in entries if e["ticker"] in whitelist))} tickers)')
    if hidden:
        hidden_tickers = sorted(set(e['ticker'] for e in entries if e['ticker'] not in whitelist))
        print(f'Hidden: {hidden} entries ({hidden_tickers})')


if __name__ == '__main__':
    main()
