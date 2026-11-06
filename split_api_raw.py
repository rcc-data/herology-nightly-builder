#!/usr/bin/env python3
"""
split_api_raw.py — turn the raw connector spill files that
fetch_tool_results.command copied into Downloads (_api_raw_<timestamp>.txt)
into the _api_<level>*.json files that api_to_csv.py expects.

Each spill file is JSON: {"ad_entities": "<json array as a string>", ...}
optionally with {"pagination": {"next_cursor": "..."}}.

LEVEL DETECTION
  has 'adset_name'                  -> ad
  every row's id == the ad account  -> account
  otherwise                         -> campaign
  NOTE: account and campaign rows have IDENTICAL key sets — at campaign level the
  campaign name comes back as 'name', not 'campaign_name'. So the ONLY reliable
  discriminator is the entity id. Don't "simplify" this back to a key check.

WHY DAY-BY-DAY (found 2026-09-20)
  The old ad-level pull (date_preset=last_30d + time_increment=1 + limit, follow the
  cursor) SILENTLY TRUNCATES. It enumerated only 184 ads and then returned an empty
  page with no cursor, i.e. it claimed to be complete. Reconciled against account
  level that was 82% of spend overall and as low as 32% on 2026-09-18 — worst on the
  most recent days, exactly where new ads launch. Dropping `limit` doesn't fix it.
  A single-day pull with NO time_increment returns the full set in ONE page with no
  cursor at all: 811 ads for 2026-09-18 = 100.0% of that day's account spend.
  So: pull one day at a time, no time_increment, and stamp the date on via --ad-dates.
  Do NOT "optimise" this back into one 30-day call.

CROSS-SESSION SAFETY
  Other Claude sessions can write to the same tool-results directory, so the
  bridge may copy pages that belong to someone else's query. Pass --only with
  the timestamps of the calls you actually made; anything else is ignored.

Run:  python3 split_api_raw.py [--downloads DIR] [--account-id ID]
                               [--only 1789957598517,1789957643296]
"""
import argparse, glob, json, os, sys

ap = argparse.ArgumentParser()
ap.add_argument('--downloads')
ap.add_argument('--account-id', default='698133907424422')
ap.add_argument('--only', help='comma-separated spill timestamps to accept; others ignored')
ap.add_argument('--ad-dates', help='comma-separated YYYY-MM-DD, paired POSITIONALLY with the '
                                   'timestamps in --only. Stamps date_start/date_stop onto ad rows '
                                   'from single-day pulls, which omit them (see WHY DAY-BY-DAY).')
args = ap.parse_args()

HERE = args.downloads or os.environ.get('HEROLOGY_DOWNLOADS')
if not HERE:
    hits = glob.glob('/sessions/*/mnt/Downloads')
    if not hits:
        sys.exit("FATAL: Downloads folder is not connected to this session.")
    HERE = hits[0]

allow = {t.strip() for t in args.only.split(',')} if args.only else None

# Positional timestamp -> date map for single-day ad pulls.
date_for = {}
if args.ad_dates:
    if not args.only:
        sys.exit("FATAL: --ad-dates requires --only (they are paired positionally).")
    ts_order = [t.strip() for t in args.only.split(',')]
    dts      = [d.strip() for d in args.ad_dates.split(',')]
    if len(ts_order) != len(dts):
        sys.exit(f"FATAL: --only has {len(ts_order)} timestamps but --ad-dates has {len(dts)} dates.")
    date_for = dict(zip(ts_order, dts))

raws = sorted(glob.glob(os.path.join(HERE, '_api_raw_*.txt')))
if not raws:
    sys.exit(f"FATAL: no _api_raw_*.txt in {HERE} — run fetch_tool_results.command on the Mac first.")


def stamp_of(path):
    return ''.join(c for c in os.path.basename(path) if c.isdigit())


def level_of(rows, account_id):
    r = rows[0]
    if 'adset_name' in r:
        return 'ad'
    if {x.get('id') for x in rows} == {account_id}:
        return 'account'
    return 'campaign'


buckets = {'account': [], 'campaign': [], 'ad': []}
cursors = []

for path in raws:
    ts = stamp_of(path)
    if allow is not None and ts not in allow:
        print(f"  {os.path.basename(path)}: SKIPPED (not in --only allowlist)")
        continue
    with open(path) as fh:
        obj = json.load(fh)
    raw = obj.get('ad_entities')
    rows = json.loads(raw) if isinstance(raw, str) else (raw or [])
    if not rows:
        print(f"  {os.path.basename(path)}: empty, skipped")
        continue
    lvl = level_of(rows, args.account_id)

    # Single-day ad pulls carry no date_start/date_stop — stamp the queried date on.
    if lvl == 'ad' and ts in date_for and 'date_start' not in rows[0]:
        d = date_for[ts]
        for r in rows:
            r['date_start'] = d
            r['date_stop']  = d

    buckets[lvl].append((ts, rows))
    cur = (obj.get('pagination') or {}).get('next_cursor')
    if cur:
        cursors.append((lvl, ts, cur))
    days = sorted({x.get('date_start') for x in rows if x.get('date_start')}) or ['(no date)']
    print(f"  {os.path.basename(path)}: {len(rows):,} rows -> {lvl:<8} "
          f"{days[0]}..{days[-1]} ({len(days)}d, {len({x.get('id') for x in rows})} entities)"
          + ("  [has next_cursor]" if cur else ""))

# Clear previous files ONLY for levels we are about to rewrite, so a short pull
# can't inherit yesterday's pages. Levels with no spill pages are left alone —
# a small response (typically account level) comes back inline and is saved
# directly by Claude, so it never has a _api_raw_*.txt and must not be deleted.
for lvl, pages in buckets.items():
    if not pages:
        continue
    for old in glob.glob(os.path.join(HERE, f'_api_{lvl}*.json')):
        os.remove(old)

print()
for lvl, pages in buckets.items():
    if not pages:
        continue
    seen, uniq = set(), []
    for ts, p in pages:
        key = json.dumps(p[:2], sort_keys=True) + str(len(p))
        if key in seen:
            print(f"  (skipped duplicate {lvl} page {ts})")
            continue
        seen.add(key)
        uniq.append(p)

    if lvl == 'account':
        rows = [r for p in uniq for r in p]
        json.dump(rows, open(os.path.join(HERE, '_api_account.json'), 'w'))
        print(f"WROTE _api_account.json  ({len(rows):,} rows)")
    else:
        total = 0
        for n, p in enumerate(uniq, 1):
            json.dump(p, open(os.path.join(HERE, f'_api_{lvl}_{n}.json'), 'w'))
            total += len(p)
            print(f"WROTE _api_{lvl}_{n}.json  ({len(p):,} rows)")
        print(f"      {lvl}: {total:,} rows across {len(uniq)} page(s)")

missing = [l for l in ('account', 'campaign', 'ad')
           if not buckets[l] and not glob.glob(os.path.join(HERE, f'_api_{l}*.json'))]
if missing:
    print(f"\nWARNING: nothing for: {', '.join(missing)} — run_nightly.py needs all three.")

if cursors:
    print("\nNEXT CURSORS (resend that level's call with cursor=, all other params unchanged):")
    for lvl, ts, cur in cursors:
        print(f"  [{lvl}] page {ts}:\n    {cur}")
else:
    print("\nNo next_cursor on any page — pagination complete.")
