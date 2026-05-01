#!/usr/bin/env python3
"""
Aggregate TradingAgents summary JSON files into a single dashboard data file.
Run from the reports directory:
  python3 aggregate_reports.py

Output: dashboard_data.json — used by dashboard.html
Also generates date list config for dashboard.html auto-discovery.
"""

import json
import glob
import os
import re
import sys


def normalize_action(action, raw_decision):
    """Normalize action to BUY/SELL/HOLD."""
    if action and action != 'UNKNOWN':
        return action
    rd = (raw_decision or '').lower()
    if any(w in rd for w in ('overweight', 'buy')):
        return 'BUY'
    if any(w in rd for w in ('underweight', 'sell')):
        return 'SELL'
    return 'HOLD'


def aggregate(reports_dir='.'):
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


def main():
    reports_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    entries = aggregate(reports_dir)

    if not entries:
        print('No entries found.', file=sys.stderr)
        sys.exit(1)

    output_path = os.path.join(reports_dir, 'dashboard_data.json')
    with open(output_path, 'w') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    # Also generate the date config snippet for dashboard.html
    dates = sorted(set((e['date'], e['variant']) for e in entries))
    date_config = json.dumps([
        {'date': d, 'variant': v} for d, v in dates
    ], indent=2)

    print(f'Wrote {len(entries)} entries ({len(dates)} date-variant combos) to {output_path}')
    print(f'\nPaste this into REPORT_DATES in dashboard.html:\nconst REPORT_DATES = {date_config};')


if __name__ == '__main__':
    main()
