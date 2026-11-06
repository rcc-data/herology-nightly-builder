#!/usr/bin/env python3
"""
reconcile.py — the nightly gate. Run BEFORE building/publishing.

Compares ad-level spend against account-level spend from the SAME pull, per day
and in total. The ad-level API pull truncates silently (see NIGHTLY_ROUTINE.md
Step 1), so this is the only thing standing between a bad pull and a published
dashboard with wrong per-creative numbers.

Exit code 0 = safe to build. Exit code 1 = DO NOT PUBLISH.

Run:  python3 reconcile.py [--downloads DIR] [--min-total 97] [--min-day 90]
"""
import argparse, glob, json, os, sys
from collections import defaultdict

ap = argparse.ArgumentParser()
ap.add_argument('--downloads')
ap.add_argument('--min-total', type=float, default=99.0, help='min overall coverage %%')
ap.add_argument('--min-day',   type=float, default=97.0, help='min per-day coverage %%')
args = ap.parse_args()

HERE = args.downloads or os.environ.get('HEROLOGY_DOWNLOADS')
if not HERE:
    hits = glob.glob('/sessions/*/mnt/Downloads')
    if not hits:
        sys.exit("FATAL: Downloads folder is not connected to this session.")
    HERE = hits[0]


def money(v):
    if v is None:
        return 0.0
    # The connector returns either a plain string ('1674.8') or the structured
    # form {'value': '1674.8', 'unit': 'EUR'} — handle both shapes.
    if isinstance(v, dict):
        v = v.get('value')
        if v is None:
            return 0.0
    s = str(v).replace('€', '').replace(' EUR', '').replace('EUR', '').replace(',', '').strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def load(level):
    files = sorted(glob.glob(os.path.join(HERE, f'_api_{level}*.json')))
    if not files:
        sys.exit(f"FATAL: no _api_{level}*.json in {HERE} — the {level} pull is missing.")
    rows = []
    for f in files:
        rows += json.load(open(f))
    return rows, files


acc_rows, acc_files = load('account')
ad_rows,  ad_files  = load('ad')

acc = {r['date_start']: money(r.get('amount_spent')) for r in acc_rows}
ad  = defaultdict(float)
for r in ad_rows:
    ad[r['date_start']] += money(r.get('amount_spent'))

ad_ids = {r['id'] for r in ad_rows}
dupes  = len(ad_rows) - len({(r['id'], r['date_start']) for r in ad_rows})

print(f"account files: {[os.path.basename(f) for f in acc_files]}")
print(f"ad files     : {[os.path.basename(f) for f in ad_files]}")
print(f"ad rows {len(ad_rows):,} | unique ads {len(ad_ids)} | days {len(ad)} | duplicate (ad,date) pairs {dupes}")
print()
print("day          ad_spend   acct_spend    cov%")

fails = []
for d in sorted(acc):
    a, c = ad.get(d, 0.0), acc[d]
    cov = 100 * a / c if c else 0.0
    flag = ''
    if c and cov < args.min_day:
        flag = '  <-- LOW'
        fails.append((d, cov))
    print(f"{d}  {a:>9,.2f}  {c:>9,.2f}  {cov:6.1f}%{flag}")

tot_ad, tot_acc = sum(ad.values()), sum(acc.values())
tot_cov = 100 * tot_ad / tot_acc if tot_acc else 0.0
print()
print(f"TOTAL ad EUR {tot_ad:,.2f} vs account EUR {tot_acc:,.2f} = {tot_cov:.2f}%")

problems = []
if tot_cov < args.min_total:
    problems.append(f"overall coverage {tot_cov:.2f}% is below {args.min_total}%")
if fails:
    problems.append(f"{len(fails)} day(s) below {args.min_day}%: " +
                    ", ".join(f"{d} ({c:.1f}%)" for d, c in fails))
if dupes:
    problems.append(f"{dupes} duplicate (ad, date) rows — chunks overlap")

missing = sorted(set(acc) - set(ad))
if missing:
    problems.append(f"{len(missing)} day(s) present at account level but absent at ad level: {missing}")

print()
if problems:
    print("RECONCILE FAILED — DO NOT PUBLISH:")
    for p in problems:
        print(f"  - {p}")
    print("\nMost likely causes:")
    print("  1. The effective_status filter was omitted -> archived ads dropped (~1% of spend).")
    print("  2. An object_ids chunk hit the row cap and truncated silently.")
    print("Check whether any chunk returned exactly `limit` rows, then re-pull with smaller chunks.")
    sys.exit(1)

print("RECONCILE PASSED — safe to build and publish.")
