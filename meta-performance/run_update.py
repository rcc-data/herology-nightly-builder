#!/usr/bin/env python3
"""
run_update.py  —  Build DASHBOARD 1 (Herology Meta Performance) from the
API-derived CSVs that api_to_csv.py writes into Downloads.

RECONSTRUCTED 2026-09-17 after the original went missing. The transform below
was reverse-engineered from the data already embedded in _dashboard_template.html
and validated against it (see VALIDATION). Behaviour is byte-compatible with the
previous builds.

HOW IT WORKS
------------
_dashboard_template.html is both the UI shell AND the accumulating data store:
three single-line JS arrays hold the full history.

    const ACC = [ ... ]     one row per day
    const CAM = [ ... ]     one row per day x campaign
    const AD  = [ ... ]     one row per day x campaign x adset x ad

The Meta API pull only covers the last 30 days, so this script UPSERTS the fresh
rows onto the existing history (same key -> replaced, older rows -> untouched),
re-emits the three arrays, and writes:

    Herology_MetaPerformance_<Mon><D><YYYY>.html   (dated build, what gets pushed)
    _dashboard_template.html                       (overwritten = new store)

The date label comes from the newest account-level date in the data.

FIELD DERIVATION (CSV column -> JS key)
---------------------------------------
    Day                                -> date
    Reach                              -> reach
    Frequency                          -> frequency
    Amount spent (EUR)                 -> spend
    Link clicks                        -> clicks
    CPC (cost per link click)          -> cpc
    Views                              -> views
    Adds to cart                       -> atc
    Cost per add to cart               -> cost_atc
    Purchases                          -> purchases
    Cost per purchase                  -> cpp
    Purchases conversion value         -> revenue
    Purchase ROAS                      -> roas
    100% Video View                    -> video_complete
    Hook Rate %       x100             -> hook_rate      (fraction -> percent)
    Hold Rate         x100             -> hold_rate      (fraction -> percent)
    Post shares / Post saves / IG      -> shares / saves / ig_follows

  COMPUTED, not taken from the CSV:
    cpm = spend * 1000 / Views         cost per 1,000 VIEWS, not impressions.
                                       (The CSV's "CPMr (EUR)" is reach-based and
                                       is NOT what this dashboard shows.)
    ctr = clicks / Reach * 100         reach-based, not impression-based.

  CAM only:  market = 'UK' if campaign name starts with 'UK_' else 'IE'
             ctype  = 'RTG' if name contains RTG/RETARGET else 'ACQ'
  AD only:   format / product / creator / creative_type from the ad-name
             convention Mon2026_TYPE_Product_Format_Vintage_Creator_Name

VALIDATION (against the 907 ACC / 5,051 CAM / 12,563 AD rows already in the store)
    cpm = spend*1000/views ....... median relative error 3e-6  (rounding only)
    ctr = clicks/reach*100 ....... median relative error 2e-5
    cpc / cpp / cost_atc / roas .. exact
    ad-name classifier ........... 269/269 distinct ad names correct
    campaign market / ctype ...... 5,051/5,051 correct

Run:  python3 run_update.py        (no arguments; reads/writes its own folder)
"""
import csv, json, os, re, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, '_dashboard_template.html')

ACC_CSV = os.path.join(HERE, 'API-Account-Level-Performance.csv')
CAM_CSV = os.path.join(HERE, 'API-Campaign-Level-Performance.csv')
AD_CSV  = os.path.join(HERE, 'API-Ad-Level-Performance.csv')

TYPE_CT = {'VID': 'Video', 'IMG': 'Image', 'CAR': 'Carousel', 'GIF': 'Gif'}
PROD_KW = [('balance', 'Balance'), ('meno', 'Meno'), ('zen', 'Zen'),
           ('ppn', 'PPN'), ('postnatal', 'PPN'), ('quiz', 'Quiz')]
MONTH_TOKEN = re.compile(r'^[A-Za-z]{3,6}\d{2,4}$')


# ── helpers ───────────────────────────────────────────────────────────────────
def f(v):
    """CSV cell -> float or None."""
    if v is None:
        return None
    s = str(v).strip().replace(',', '')
    if s in ('', '-', 'nan', 'None'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def rnd(v, n):
    return None if v is None else round(v, n)


def div(a, b):
    return (a / b) if (a is not None and b) else None


def classify_ad(name):
    """ad name -> (format, product, creator, creative_type)."""
    parts = (name or '').split('_')
    if len(parts) >= 2 and MONTH_TOKEN.match(parts[0]) and parts[1] in TYPE_CT:
        return (parts[1],
                parts[2] if len(parts) >= 3 else 'Other',
                parts[5] if len(parts) >= 6 else 'NoCreator',
                TYPE_CT[parts[1]])
    low = (name or '').lower()
    product = 'Other'
    for kw, label in PROD_KW:
        if kw in low:
            product = label
            break
    return 'Other', product, 'NoCreator', 'Other'


def market_of(campaign):
    return 'UK' if (campaign or '').upper().startswith('UK_') else 'IE'


def ctype_of(campaign):
    n = (campaign or '').upper()
    return 'RTG' if ('RTG' in n or 'RETARGET' in n) else 'ACQ'


def base_row(r):
    """Shared metric block, identical at every level."""
    reach  = f(r.get('Reach'))
    spend  = f(r.get('Amount spent (EUR)')) or f(r.get('Amount spent (USD)'))
    clicks = f(r.get('Link clicks'))
    views  = f(r.get('Views'))
    hook   = f(r.get('Hook Rate %'))
    hold   = f(r.get('Hold Rate'))
    return {
        'reach':          reach,
        'frequency':      rnd(f(r.get('Frequency')), 4),
        'spend':          rnd(spend, 2),
        'clicks':         clicks,
        'cpc':            rnd(f(r.get('CPC (cost per link click)')), 4),
        'views':          views,
        'atc':            f(r.get('Adds to cart')),
        'cost_atc':       rnd(f(r.get('Cost per add to cart')), 4),
        'purchases':      f(r.get('Purchases')),
        'cpp':            rnd(f(r.get('Cost per purchase')), 4),
        'revenue':        rnd(f(r.get('Purchases conversion value')), 2),
        'roas':           rnd(f(r.get('Purchase ROAS (return on ad spend)')), 4),
        # cost per 1,000 VIEWS — deliberately not the CSV's reach-based CPMr
        'cpm':            rnd(div(spend * 1000 if spend is not None else None, views), 4),
        'video_complete': rnd(f(r.get('100% Video View')), 4),
        'hook_rate':      rnd(hook * 100, 2) if hook is not None else None,
        'hold_rate':      rnd(hold * 100, 2) if hold is not None else None,
        'shares':         f(r.get('Post shares')),
        'saves':          f(r.get('Post saves')),
        'ig_follows':     f(r.get('Instagram follows')),
        # reach-based CTR
        'ctr':            rnd(div(clicks * 100 if clicks is not None else None, reach), 4),
    }


def read_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8-sig', newline='') as fh:
        return [r for r in csv.DictReader(fh) if (r.get('Day') or '').strip()]


# ── build the three levels ────────────────────────────────────────────────────
def build_acc(rows):
    out = []
    for r in rows:
        out.append({'date': r['Day'].strip(), **base_row(r)})
    return out


def build_cam(rows):
    out = []
    skipped = 0
    for r in rows:
        camp = (r.get('Campaign name') or '').strip()
        if not camp:
            # A nameless campaign row would collapse every day onto one key and
            # pollute the store. Refuse it rather than write junk.
            skipped += 1
            continue
        out.append({'date': r['Day'].strip(), 'campaign': camp,
                    'market': market_of(camp), 'ctype': ctype_of(camp),
                    **base_row(r)})
    if skipped:
        sys.exit(f"FATAL: {skipped} campaign row(s) have no Campaign name — "
                 "check api_to_csv.py's campaign_name mapping. Refusing to build.")
    return out


def build_ad(rows):
    out = []
    for r in rows:
        camp  = (r.get('Campaign name') or '').strip()
        adset = (r.get('Ad set name') or '').strip()
        ad    = (r.get('Ad name') or '').strip()
        fmt, product, creator, ctype = classify_ad(ad)
        out.append({'date': r['Day'].strip(), 'campaign': camp, 'adset': adset,
                    'ad': ad, 'format': fmt, 'product': product,
                    'creator': creator, 'creative_type': ctype,
                    **base_row(r)})
    return out


# ── template read / upsert / write ────────────────────────────────────────────
PREFIXES = {'ACC': 'const ACC = [', 'CAM': 'const CAM = [', 'AD': 'const AD  = ['}


def locate(lines):
    idx = {}
    for i, line in enumerate(lines):
        for key, pre in PREFIXES.items():
            if key not in idx and line.startswith(pre):
                idx[key] = i
    missing = [k for k in PREFIXES if k not in idx]
    if missing:
        sys.exit(f"FATAL: could not find {missing} data block(s) in _dashboard_template.html")
    return idx


def parse_block(line):
    return json.loads(line[line.index('['):line.rindex(']') + 1])


def upsert(old, new, keys):
    """Replace rows whose key matches a fresh row; keep the rest; sort by key."""
    def k(r):
        return tuple(r.get(x) for x in keys)
    merged = {k(r): r for r in old}
    replaced = sum(1 for r in new if k(r) in merged)
    for r in new:
        merged[k(r)] = r
    rows = sorted(merged.values(), key=lambda r: tuple(str(r.get(x) or '') for x in keys))
    return rows, replaced, len(new) - replaced


def main():
    if not os.path.exists(TEMPLATE):
        sys.exit("FATAL: _dashboard_template.html not found in Downloads.")

    acc_rows = read_csv(ACC_CSV)
    cam_rows = read_csv(CAM_CSV)
    ad_rows  = read_csv(AD_CSV)
    if not acc_rows or not cam_rows:
        sys.exit("FATAL: API-Account-Level / API-Campaign-Level CSV missing or empty. "
                 "Run api_to_csv.py first.")

    print("Building Meta Performance dashboard:")
    new_acc = build_acc(acc_rows)
    new_cam = build_cam(cam_rows)
    new_ad  = build_ad(ad_rows) if ad_rows else []
    print(f"  fresh rows from API — account {len(new_acc)}, campaign {len(new_cam)}, ad {len(new_ad)}")

    text = open(TEMPLATE, encoding='utf-8').read()
    lines = text.split('\n')
    idx = locate(lines)

    old_acc = parse_block(lines[idx['ACC']])
    old_cam = parse_block(lines[idx['CAM']])
    old_ad  = parse_block(lines[idx['AD']])
    print(f"  existing store    — account {len(old_acc)}, campaign {len(old_cam)}, ad {len(old_ad)}")

    acc, a_up, a_new = upsert(old_acc, new_acc, ['date'])
    cam, c_up, c_new = upsert(old_cam, new_cam, ['date', 'campaign'])
    if new_ad:
        ad, d_up, d_new = upsert(old_ad, new_ad, ['date', 'campaign', 'adset', 'ad'])
    else:
        ad, d_up, d_new = old_ad, 0, 0
        print("  ! no ad-level CSV — keeping existing ad rows untouched")

    print(f"  merged            — account {len(acc)} ({a_up} updated, {a_new} added), "
          f"campaign {len(cam)} ({c_up} updated, {c_new} added), "
          f"ad {len(ad)} ({d_up} updated, {d_new} added)")

    dumps = lambda obj: json.dumps(obj, separators=(',', ':'), ensure_ascii=False)
    lines[idx['ACC']] = 'const ACC = ' + dumps(acc) + ';'
    lines[idx['CAM']] = 'const CAM = ' + dumps(cam) + ';'
    lines[idx['AD']]  = 'const AD  = ' + dumps(ad) + ';'
    out_text = '\n'.join(lines)

    max_date = max(r['date'] for r in acc)
    d = datetime.strptime(max_date, '%Y-%m-%d')
    label = f"{d.strftime('%b')}{d.day}{d.year}"
    out_path = os.path.join(HERE, f'Herology_MetaPerformance_{label}.html')

    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(out_text)
    with open(TEMPLATE, 'w', encoding='utf-8') as fh:
        fh.write(out_text)

    size = os.path.getsize(out_path) / 1024 / 1024
    print(f"  data through {max_date}")
    print(f"  wrote {os.path.basename(out_path)} ({size:.1f} MB)")
    print("  updated _dashboard_template.html")


if __name__ == '__main__':
    main()
