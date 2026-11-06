#!/usr/bin/env python3
"""
segments_to_csv.py - Audience-segment (New / Engaged / Existing) spend split.  Added 2026-10-22 (Ryan).

Meta's `user_segment_key` breakdown ("Audience segments") returns: prospecting (= New), engaged
(still a new customer, has engaged with the brand), existing (existing customers), unknown.

Inputs (saved by Claude to Downloads, see NIGHTLY_ROUTINE.md Step 1c):
  _seg_account*.json  level=ad_account, breakdowns=[user_segment_key], time_increment=1,
                       time_range 2026-01-01 -> yesterday  (fields amount_spent, omni_purchase, omni_purchase_values)
  _seg_adday*.json  level=ad, breakdowns=[user_segment_key], date_preset=last_14d (NO time_increment),
                       object_ids chunks; fields campaign_name, adset_name, name, amount_spent, omni_purchase,
                       omni_purchase_values, impressions, reach
Outputs (creative-hub-engine/):
  segments_master.csv  Day,Segment,Spend,Purchases,Value   (upserted; Segment in new|engaged|existing|unknown)
  ad_segments.json     {"window":[start,end],"ads":{ad_name:{new:{s,p,v},engaged:{..},existing:{..},unknown:{..}}}}
Exit 1 only if the account-level file is missing (the KPI would be stale); the ad file is optional.
"""
import glob, json, csv, os, sys, collections

def find_mount(name):
    # Cloud runs (herology-nightly-builder repo) set HEROLOGY_DOWNLOADS / HEROLOGY_META_ADS;
    # Mac runs fall back to the session mounts exactly as before.
    env = {'Downloads': 'HEROLOGY_DOWNLOADS', 'Meta Ads': 'HEROLOGY_META_ADS'}.get(name)
    if env and os.environ.get(env):
        return os.environ[env]
    hits = glob.glob(f'/sessions/*/mnt/{name}') or glob.glob(os.path.expanduser(f'~/mnt/{name}'))
    if not hits: sys.exit(f"FATAL: '{name}' folder is not connected.")
    return hits[0]
DOWNLOADS = find_mount('Downloads'); META_ADS = find_mount('Meta Ads')
ENGINE = os.path.join(META_ADS, 'creative-hub-engine')
SEGMAP = {'prospecting': 'new', 'engaged': 'engaged', 'existing': 'existing', 'unknown': 'unknown'}

def ents(raw):
    if isinstance(raw, dict) and 'ad_entities' in raw:
        e = raw['ad_entities']; return json.loads(e) if isinstance(e, str) else e
    return raw if isinstance(raw, list) else []
def val(x):
    if x is None: return 0.0
    if isinstance(x, dict): x = x.get('value')
    try: return float(x)
    except (TypeError, ValueError): return 0.0
def load(pattern):
    out = []
    for p in sorted(glob.glob(os.path.join(DOWNLOADS, pattern))):
        out.extend(ents(json.load(open(p, encoding='utf-8'))))
    return out

# ---- account-level daily ----
rows = load('_seg_account*.json')
if not rows:
    sys.exit("FATAL: _api_segments.json not found/empty in Downloads - run NIGHTLY_ROUTINE.md Step 1c.")
mp = os.path.join(ENGINE, 'segments_master.csv')
master = {}
if os.path.exists(mp):
    for r in csv.DictReader(open(mp, encoding='utf-8')): master[(r['Day'], r['Segment'])] = r
for r in rows:
    seg = SEGMAP.get(r.get('user_segment_key'), 'unknown'); day = r['date_start']
    k = (day, seg)
    prev = master.get(k)
    master[k] = {'Day': day, 'Segment': seg, 'Spend': round(val(r.get('amount_spent')), 2),
                 'Purchases': int(val(r.get('omni_purchase'))), 'Value': round(val(r.get('omni_purchase_values')), 2)}
with open(mp, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=['Day', 'Segment', 'Spend', 'Purchases', 'Value']); w.writeheader()
    for k in sorted(master): w.writerow(master[k])
days = sorted({k[0] for k in master})
last14 = [d for d in days][-14:]
tot = collections.defaultdict(float)
for (d, s), r in master.items():
    if d in last14: tot[s] += float(r['Spend'])
t = sum(tot.values()) or 1
print(f"segments_master.csv: {len(master)} rows, {days[0]} -> {days[-1]}")
print("last-14d spend split: " + " | ".join(f"{s} {tot[s]/t*100:.1f}%" for s in ('new', 'engaged', 'existing', 'unknown')) + f"  (total EUR {t:,.2f})")

# ---- ad-level DAILY split (follows the report date range) ----
# Input _seg_adday*.json: list of [day, ad_name, segment_key, spend]  (engaged folded into New, null/unknown -> unknown)
out = os.path.join(ENGINE, 'ad_segments.json')
res = {'ads': {}}
if os.path.exists(out):
    try: res = json.load(open(out))
    except Exception: pass
res.pop('window', None)
ads = res.setdefault('ads', {})
inp = []
for pth in sorted(glob.glob(os.path.join(DOWNLOADS, '_seg_adday*.json'))): inp.extend(json.load(open(pth, encoding='utf-8')))
if inp:
    newdays = {r[0] for r in inp}
    for n in list(ads):                       # replace whole days that were re-pulled
        for d in newdays: ads[n].pop(d, None)
    for day, name, sk, sp in inp:
        seg = {'prospecting': 0, 'engaged': 0, 'existing': 1}.get(sk, 2)
        ads.setdefault(name, {}).setdefault(day, [0.0, 0.0, 0.0])[seg] += float(sp)
    for n in list(ads):
        ads[n] = {d: [round(x, 2) for x in v] for d, v in ads[n].items()}
        if not ads[n]: del ads[n]
    json.dump(res, open(out, 'w'), separators=(',', ':'))
    alld = sorted({d for v in ads.values() for d in v})
    print(f"ad_segments.json: {len(ads)} ad names, days {alld[0]} -> {alld[-1]}")
else:
    print("WARNING: no _seg_adday*.json - keeping previous ad_segments.json (per-ad split may be stale)")
    if not os.path.exists(out): json.dump({'ads': {}}, open(out, 'w'))
