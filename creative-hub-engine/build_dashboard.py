#!/usr/bin/env python3
"""
Build the Herology Creative Hub dashboard.

Usage:
  python3 build_dashboard.py \\
    --meta-csv /path/to/Meta-export.csv \\
    --hub-csv /path/to/Herology_Creative_Hub_-_Ad_Sheet.csv \\
    --output /mnt/user-data/outputs/index.html

Inputs:
  --meta-csv  : Meta Ads Manager daily ad-level export (CSV)
  --hub-csv   : Herology Creative Hub Ad Sheet (CSV from the Google Sheet)
  --output    : Where to write the final HTML dashboard

Optional:
  --logo      : Path to brand logo PNG (default: ./Herology_Logo.png)
  --mapping   : Path to baked video mapping JSON (default: ./video_mapping.json)
  --template  : Path to HTML template (default: ./dashboard_template.html)
  --r2-base   : R2 bucket public URL (default: the Herology bucket)
"""
import argparse, csv, json, re, base64, sys, os
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

DEFAULT_R2_BASE = "https://pub-70583d87d1ae49d78024fb434a4421a2.r2.dev/"

PRODUCTS = ['balance', 'meno', 'zen', 'creatine', 'ppn', 'collagen']

def to_float(x):
    try: return float(x) if x not in (None, '', ' ') else 0
    except (TypeError, ValueError): return 0

def detect_product(s):
    s_lower = (s or '').lower()
    for p in PRODUCTS:
        if p in s_lower: return p
    return None

def normalize(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())

def core_id(s):
    s = (s or '').lower()
    s = re.sub(r'^[a-z]{3}_?\d{2,4}_?', '', s)
    s = re.sub(r'^(vid|img|gif|car)_', '', s)
    s = re.sub(r'[-_](partnership(-rtg)?|rtg|copy|v\d+|final|clean|test)', '', s)
    s = re.sub(r'^test\s*-?\s*', '', s)
    s = re.sub(r'\s*-?\s*copy\d*$', '', s)
    s = re.sub(r'\([^)]+\)', '', s)
    s = re.sub(r'[^a-z0-9]', '', s)
    return s


def parse_meta_csv(meta_csv_path):
    """Parse Meta Ads daily CSV. Returns daily_rows[ad][day] = metrics."""
    daily_rows = defaultdict(lambda: defaultdict(lambda: {
        'spend': 0, 'purchases': 0, 'value': 0, 'reach': 0, 'clicks': 0,
        'views': 0, 'atc': 0, 'video_views_100': 0, 'shares': 0, 'saves': 0,
        'follows': 0, 'likes': 0, 'campaigns': set(),
        'hook_rates': [], 'hold_rates': [], 'frequencies': [],
    }))

    with open(meta_csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ad = row.get('Ad name')
            day = row.get('Day') or row.get('Reporting starts')
            if not ad or not day:
                continue
            d = daily_rows[ad][day]
            d['spend'] += to_float(row.get('Amount spent (EUR)') or row.get('Amount spent (USD)'))
            d['purchases'] += to_float(row.get('Purchases'))
            d['value'] += to_float(row.get('Purchases conversion value'))
            d['reach'] += to_float(row.get('Reach'))
            d['clicks'] += to_float(row.get('Link clicks'))
            d['views'] += to_float(row.get('Views') or row.get('Video views') or row.get('3-second video plays'))
            d['atc'] += to_float(row.get('Adds to cart'))
            d['video_views_100'] += to_float(row.get('100% Video View') or row.get('Video plays at 100%'))
            d['shares'] += to_float(row.get('Post shares'))
            d['saves'] += to_float(row.get('Post saves'))
            d['follows'] += to_float(row.get('Instagram follows'))
            d['likes'] += to_float(row.get('Post likes'))
            if row.get('Campaign name'):
                d['campaigns'].add(row['Campaign name'])
            if row.get('Hook Rate %'):
                d['hook_rates'].append(to_float(row['Hook Rate %']))
            if row.get('Hold Rate'):
                d['hold_rates'].append(to_float(row['Hold Rate']))
            if row.get('Frequency'):
                d['frequencies'].append(to_float(row['Frequency']))

    return daily_rows


def parse_account_csv(account_csv_path):
    """Parse the account-level daily CSV into {day: {spend, purchases, value}}.

    Account-level totals are the source of truth for the header KPIs (Meta
    dedupes across ads at the account level, so summing ad-level rows
    undercounts). One row per Day. Later rows for the same Day overwrite.
    """
    daily = {}
    with open(account_csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            day = (row.get('Day') or '').strip()
            if not day:
                continue
            daily[day] = {
                'spend': to_float(row.get('Amount spent (EUR)')),
                'purchases': to_float(row.get('Purchases')),
                'value': to_float(row.get('Purchases conversion value')),
                'clicks': to_float(row.get('Link clicks')),
                'views': to_float(row.get('Views')),
                'saves': to_float(row.get('Post saves')),
                'shares': to_float(row.get('Post shares')),
                'likes': to_float(row.get('Post likes')),
                'follows': to_float(row.get('Instagram follows')),
            }
    return daily


def parse_hub_csv(hub_csv_path):
    """Parse Hub Ad Sheet CSV. Returns list of hub rows.

    Deduplicates by Ad Name: the sheet sometimes has the same ad entered on
    multiple rows (e.g. one with a Creative Link, one without). We merge those
    into a single row, filling empty fields from later duplicates, so each ad
    produces exactly one card. First occurrence sets card order.
    """
    by_name = {}
    order = []
    with open(hub_csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get('Ad Name') or '').strip()
            if not name:
                continue
            if name not in by_name:
                by_name[name] = dict(row)
                order.append(name)
            else:
                existing = by_name[name]
                for k, v in row.items():
                    if (not (existing.get(k) or '').strip()) and (v or '').strip():
                        existing[k] = v
    return [by_name[n] for n in order]


def parse_ad_name(name):
    """Parse a Meta or Hub ad name into structured parts.

    Standard format: Mon2026_TYPE_Product_Format_Vintage_Creator_CreativeName(-Variant)(-Partnership/-RTG/-Copy)
    Returns dict with keys: month, type, product, format, vintage, creator, creative_name, raw_suffix.
    Falls back gracefully for non-standard names.
    """
    if not name:
        return None
    raw = name
    # Strip known suffix tokens off the end before splitting
    # These do NOT change which creative the ad is — Partnership/RTG are distribution flags
    suffix_pattern = re.compile(
        r'\s*[-–—]?\s*(Partnership(-RTG)?|RTG|Copy(\d+)?|[-–—]\s*Copy(\d+)?|\(\d+\)|\s*-\s*Copy\s*\d*)\s*$',
        re.IGNORECASE
    )
    name_clean = name
    # Strip suffixes iteratively (handles "-Partnership – Copy")
    for _ in range(5):
        new = suffix_pattern.sub('', name_clean).strip()
        if new == name_clean:
            break
        name_clean = new

    parts = name_clean.split('_')
    if len(parts) >= 7 and re.match(r'^[A-Za-z]{3,4}\d{2,4}$', parts[0]):
        # Standard format
        return {
            'raw': raw,
            'clean': name_clean,
            'month': parts[0],
            'type': parts[1],          # VID/IMG/GIF/CAR
            'product': parts[2],        # Balance/Meno/Zen/...
            'format': parts[3],         # UGC/Product/Features/Review/Message/Infographic/Q&A...
            'vintage': parts[4],        # Evergreen/etc.
            'creator': parts[5],        # Michelle/Sarah/Niamh/NoCreator/etc.
            'creative_name': '_'.join(parts[6:]),  # The Balance-XYZ part
            'structured': True,
        }
    # Non-standard (test ads, shorthand names from early Meta era)
    return {
        'raw': raw,
        'clean': name_clean,
        'structured': False,
    }


def creative_name_key(creative_name):
    """Normalize the trailing creative-name segment for comparison.
    Strips dashes/spaces/underscores and lowercases.
    e.g. "Balance-TTComment-DrSarahKelly" -> "balancettcommentdrsarahkelly"
    """
    return re.sub(r'[^a-z0-9]', '', (creative_name or '').lower())


def creator_matches(a, b, creative_name_a=None, creative_name_b=None):
    """Compare two creator tokens loosely — handles Dr prefixes, last-name only, etc.
    If creative_names are provided, also accepts a match when one side is 'NoCreator'
    and the creator appears in the other side's creative_name.
    """
    if not a or not b:
        return a == b
    na = re.sub(r'^dr', '', a.lower())
    nb = re.sub(r'^dr', '', b.lower())
    na = re.sub(r'[^a-z0-9]', '', na)
    nb = re.sub(r'[^a-z0-9]', '', nb)
    if na == nb:
        return True
    # Substring (e.g., "michelle" matches "michellehone")
    if na and nb and (na in nb or nb in na):
        return True
    # Handle NoCreator slot when the actual creator lives in the creative name
    if 'nocreator' in (na, nb) and (creative_name_a or creative_name_b):
        # The "real" creator is the other side
        real = nb if na == 'nocreator' else na
        other_creative = (creative_name_a if na == 'nocreator' else creative_name_b) or ''
        if real and real in re.sub(r'[^a-z0-9]', '', other_creative.lower()):
            return True
    return False


def load_legacy_overrides(overrides_path=None):
    """Load known good shorthand → hub-ad-name mappings. Used for legacy Meta ads
    that don't follow the modern naming convention."""
    if not overrides_path:
        overrides_path = Path(__file__).parent / 'legacy_overrides.json'
    overrides_path = Path(overrides_path)
    if not overrides_path.exists():
        return {}
    try:
        with open(overrides_path) as f:
            return json.load(f)
    except Exception:
        return {}


_LEGACY_OVERRIDES_CACHE = None
def _legacy_overrides():
    global _LEGACY_OVERRIDES_CACHE
    if _LEGACY_OVERRIDES_CACHE is None:
        _LEGACY_OVERRIDES_CACHE = load_legacy_overrides()
    return _LEGACY_OVERRIDES_CACHE


def match_meta_to_hub(meta_ad, hub_rows, hub_set, hub_names):
    """Strict structural matcher. Returns hub ad name or None (no match).

    Rules:
      1. Exact name match → match
      2. Legacy override table (for confirmed shorthand pre-naming-convention ads)
      3. Strip distribution suffixes (-Partnership, -RTG, -Copy, etc.); exact match → match
      4. Same product + same creator + same creative_name (case/dash insensitive) → match
         (handles cross-month re-tags like May2026 → Mar2026 of the same creative)
      5. Non-standard Meta names with no override → no match
      6. Otherwise → no match. Better to leave unmatched in the Data Table than to mis-attribute.
    """
    if not meta_ad:
        return None

    # Rule 1: exact match
    if meta_ad in hub_set:
        return meta_ad

    # Rule 2: legacy override
    overrides = _legacy_overrides()
    if meta_ad in overrides:
        target = overrides[meta_ad]
        if target in hub_set:
            return target

    # Parse
    m_parts = parse_ad_name(meta_ad)
    if not m_parts:
        return None

    # Rule 3: exact after suffix-stripping
    if m_parts['clean'] in hub_set:
        return m_parts['clean']
    for h in hub_names:
        if h.lower() == m_parts['clean'].lower():
            return h

    # Rule 4: structural match on product + creator + creative_name
    if not m_parts.get('structured'):
        return None  # Non-standard Meta name without override: leave unmatched

    m_product = m_parts['product'].lower()
    m_creator = m_parts['creator']
    m_creative_key = creative_name_key(m_parts['creative_name'])

    candidates = []
    for h in hub_rows:
        h_parts = parse_ad_name(h['Ad Name'])
        if not h_parts or not h_parts.get('structured'):
            continue
        if h_parts['product'].lower() != m_product:
            continue
        if not creator_matches(m_creator, h_parts['creator'],
                                creative_name_a=m_parts['creative_name'],
                                creative_name_b=h_parts['creative_name']):
            continue
        if creative_name_key(h_parts['creative_name']) != m_creative_key:
            continue
        candidates.append(h['Ad Name'])

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # Ambiguous — multiple hub rows share the same product+creator+creative_name.
        # Prefer the one with matching format/vintage if possible.
        m_format = m_parts['format'].lower()
        format_matches = [c for c in candidates if parse_ad_name(c)['format'].lower() == m_format]
        if len(format_matches) == 1:
            return format_matches[0]
        m_month = m_parts['month'].lower()
        month_matches = [c for c in candidates if parse_ad_name(c)['month'].lower() == m_month]
        if len(month_matches) == 1:
            return month_matches[0]
        return sorted(candidates)[0]
    return None




def merge_daily(meta_ads, daily_rows):
    """Sum daily metrics for multiple Meta ads that map to one Hub creative."""
    merged = defaultdict(lambda: {
        'spend': 0, 'purchases': 0, 'value': 0, 'reach': 0, 'clicks': 0,
        'views': 0, 'atc': 0, 'video_views_100': 0, 'shares': 0, 'saves': 0,
        'follows': 0, 'likes': 0, 'campaigns': set(),
        'hook_rates': [], 'hold_rates': [], 'frequencies': [],
    })
    for meta_ad in meta_ads:
        for day, d in daily_rows[meta_ad].items():
            m = merged[day]
            for k in ['spend','purchases','value','reach','clicks','views','atc',
                      'video_views_100','shares','saves','follows','likes']:
                m[k] += d[k]
            m['campaigns'] |= d['campaigns']
            m['hook_rates'] += d['hook_rates']
            m['hold_rates'] += d['hold_rates']
            m['frequencies'] += d['frequencies']
    return merged


def build_cards(daily_rows, hub_rows):
    """Build card list (video creatives only) joining hub + Meta data."""
    hub_names = [r['Ad Name'] for r in hub_rows]
    hub_set = set(hub_names)

    hub_to_meta = defaultdict(list)
    for meta_ad in daily_rows:
        h = match_meta_to_hub(meta_ad, hub_rows, hub_set, hub_names)
        if h:
            hub_to_meta[h].append(meta_ad)

    cards = []
    for hub_row in hub_rows:
        if hub_row.get('Ad Type') not in ('VID', 'GIF', 'IMG', 'CAR'):
            continue
        name = hub_row['Ad Name']
        matched_meta = hub_to_meta.get(name, [])
        daily_series = {}
        if matched_meta:
            merged = merge_daily(matched_meta, daily_rows)
            for day, m in merged.items():
                daily_series[day] = {
                    'spend': round(m['spend'], 2),
                    'purchases': round(m['purchases'], 0),
                    'value': round(m['value'], 2),
                    'reach': int(m['reach']),
                    'clicks': int(m['clicks']),
                    'views': int(m['views']),
                    'atc': int(m['atc']),
                    'video_views_100': int(m['video_views_100']),
                    'shares': int(m['shares']),
                    'saves': int(m['saves']),
                    'follows': int(m['follows']), 'likes': int(m['likes']),
                    'campaigns': sorted(list(m['campaigns'])),
                    'hook_rate_avg': round(sum(m['hook_rates'])/len(m['hook_rates']), 4) if m['hook_rates'] else 0,
                    'hold_rate_avg': round(sum(m['hold_rates'])/len(m['hold_rates']), 4) if m['hold_rates'] else 0,
                    'frequency_avg': round(sum(m['frequencies'])/len(m['frequencies']), 2) if m['frequencies'] else 0,
                }
        cards.append({
            'ad_name': name,
            'theme': hub_row.get('Theme', ''),
            'product': hub_row.get('Product', ''),
            'ad_type': hub_row.get('Ad Type', ''),
            'dimensions': hub_row.get('Dimensions', ''),
            'creative_preview': hub_row.get('Creative Preview', ''),
            'primary_text': hub_row.get('Primary Text', ''),
            'headline': hub_row.get('Headline', ''),
            'description': hub_row.get('Description', ''),
            'cta': hub_row.get('CTA', ''),
            'landing_page': hub_row.get('Landing Page', ''),
            'creative_link': hub_row.get('Creative Link', ''),
            'built': hub_row.get('Built (Y/N)', ''),
            'live_date': hub_row.get('Live Date', ''),
            'daily': daily_series,
            'meta_ad_names': matched_meta,
            'is_dpa': False
        })

    return cards, hub_to_meta


def build_meta_ads_full(daily_rows, hub_to_meta):
    """Build a flat list of every Meta ad with its daily series.
    Used by the Data Table view to show every ad row from the CSV.
    Includes which hub ad it was matched to (if any)."""
    meta_to_hub = {}
    for hub_ad, meta_list in hub_to_meta.items():
        for meta_ad in meta_list:
            meta_to_hub[meta_ad] = hub_ad

    meta_ads = []
    for meta_ad, days_dict in daily_rows.items():
        daily_series = {}
        for day, d in days_dict.items():
            daily_series[day] = {
                'spend': round(d['spend'], 2),
                'purchases': round(d['purchases'], 0),
                'value': round(d['value'], 2),
                'reach': int(d['reach']),
                'clicks': int(d['clicks']),
                'views': int(d['views']),
                'atc': int(d['atc']),
                'video_views_100': int(d['video_views_100']),
                'shares': int(d['shares']),
                'saves': int(d['saves']),
                'follows': int(d['follows']), 'likes': int(d['likes']),
                'campaigns': sorted(list(d['campaigns'])),
                'hook_rate_avg': round(sum(d['hook_rates'])/len(d['hook_rates']), 4) if d['hook_rates'] else 0,
                'hold_rate_avg': round(sum(d['hold_rates'])/len(d['hold_rates']), 4) if d['hold_rates'] else 0,
                'frequency_avg': round(sum(d['frequencies'])/len(d['frequencies']), 2) if d['frequencies'] else 0,
            }
        meta_ads.append({
            'meta_ad_name': meta_ad,
            'matched_hub_ad': meta_to_hub.get(meta_ad),
            'daily': daily_series,
        })
    # Sort alphabetically for stable display
    meta_ads.sort(key=lambda x: x['meta_ad_name'])
    return meta_ads


def build(meta_csv, hub_csv, output_path, template_path, mapping_path, logo_path, r2_base, account_csv=None, new_ads_file=None, platform='meta', segments_csv=None, ad_segments=None):
    print(f"Reading Meta CSV: {meta_csv}")
    daily_rows = parse_meta_csv(meta_csv)
    print(f"  → {len(daily_rows)} unique Meta ads")

    account_daily = {}
    if account_csv and os.path.exists(account_csv):
        account_daily = parse_account_csv(account_csv)
        print(f"Reading Account CSV: {account_csv}")
        print(f"  → {len(account_daily)} account-level days (drives header KPIs)")

    print(f"Reading Hub CSV: {hub_csv}")
    hub_rows = parse_hub_csv(hub_csv)
    print(f"  → {len(hub_rows)} hub rows")

    print("Building cards...")
    cards, hub_to_meta = build_cards(daily_rows, hub_rows)
    matched_meta_count = sum(len(v) for v in hub_to_meta.values())
    print(f"  → {len(cards)} creative cards (VID/IMG/CAR)")
    print(f"  → matched {matched_meta_count}/{len(daily_rows)} Meta ads to hub creatives")
    print(f"  → {sum(1 for c in cards if c['daily'])} cards have performance data")

    if platform == 'tiktok':
        import re as _re
        _emo = {'balance':'🌸','collagen':'✨','creatine':'💪','inositol':'🧬','ppn':'🤰','zen':'🌙','meno':'🍂','mensprenatal':'👨','brand':'💚'}
        matched = {m for v in hub_to_meta.values() for m in v}
        for raw in sorted(daily_rows):
            if raw in matched or not any(d.get('spend',0) > 0 for d in daily_rows[raw].values()):
                continue
            base = _re.split(r'\.(?:MOV|MP4|mov|mp4)', raw)[0]
            base = _re.sub(r'_[A-Za-z0-9]{8}$', '', base)
            pretty = _re.sub(r'\s+', ' ', base.replace('_', ' ')).strip()
            if 'MultiVariation' in raw and not pretty.lower().startswith(('balance','collagen')):
                pretty = pretty
            prod = next((k for k in ('Inositol','Creatine','Collagen','Balance','PPN','Zen','Meno') if k.lower() in raw.lower()), 'Balance')
            nm = pretty
            n = 2
            while nm in hub_to_meta: nm = pretty + ' (' + str(n) + ')'; n += 1
            hub_to_meta[nm] = [raw]
            merged = merge_daily([raw], daily_rows)
            ds = {}
            for day, m in merged.items():
                ds[day] = {'spend': round(m['spend'], 2), 'purchases': round(m['purchases'], 0), 'value': round(m['value'], 2),
                    'reach': int(m['reach']), 'clicks': int(m['clicks']), 'views': int(m['views']), 'atc': int(m['atc']),
                    'video_views_100': int(m['video_views_100']), 'shares': int(m['shares']), 'saves': int(m['saves']),
                    'follows': int(m['follows']), 'likes': int(m['likes']), 'campaigns': sorted(list(m['campaigns'])),
                    'hook_rate_avg': round(sum(m['hook_rates'])/len(m['hook_rates']), 4) if m['hook_rates'] else 0,
                    'hold_rate_avg': round(sum(m['hold_rates'])/len(m['hold_rates']), 4) if m['hold_rates'] else 0,
                    'frequency_avg': round(sum(m['frequencies'])/len(m['frequencies']), 2) if m['frequencies'] else 0}
            cards.append({'ad_name': nm, 'theme': 'Evergreen', 'product': prod, 'ad_type': 'VID', 'dimensions': '9:16',
                'creative_preview': nm, 'primary_text': '', 'headline': '', 'description': '', 'cta': '', 'landing_page': '',
                'creative_link': '', 'built': 'Y', 'live_date': '', 'daily': ds, 'meta_ad_names': [raw], 'is_dpa': False,
                'placeholder_emoji': _emo.get(prod.lower(), '🎬')})
        print(f"  → TikTok: added multivariation/legacy cards, total {len(cards)}")

    if platform == 'tiktok':
        cards = [c for c in cards if c['daily']]
        print(f"  → TikTok: kept {len(cards)} cards with data")

    # Build flat Meta ad list for the Data Table view
    meta_ads_full = build_meta_ads_full(daily_rows, hub_to_meta)
    print(f"  → {len(meta_ads_full)} Meta ads available for Data Table view")

    # Compute date range and campaigns
    all_days = set()
    all_camps = set()
    for c in cards:
        for day, d in c['daily'].items():
            all_days.add(day)
            all_camps |= set(d['campaigns'])
    days_sorted = sorted(all_days)
    earliest = days_sorted[0] if days_sorted else '2026-01-01'
    latest = days_sorted[-1] if days_sorted else '2026-12-31'
    print(f"  → date range: {earliest} → {latest}")
    print(f"  → {len(all_camps)} unique campaigns")

    # Load mapping
    print(f"Reading mapping: {mapping_path}")
    with open(mapping_path) as f:
        baked_mapping = json.load(f)
    print(f"  → {len(baked_mapping)} baked video mappings")

    # Load "new ads" (created in Meta in the last 7 days) if present
    new_ads_cards = []
    new_ads_path = Path(new_ads_file) if new_ads_file else Path(__file__).parent / 'new_ads.json'
    if new_ads_path.exists():
        try:
            with open(new_ads_path) as nf:
                na = json.load(nf)
            for e in na:
                nm = e.get('name','')
                p = parse_ad_name(nm) or {}
                new_ads_cards.append({
                    'ad_name': nm,
                    'product': p.get('product','') or '',
                    'ad_type': p.get('type','') or ('VID' if '_VID_' in nm else 'IMG'),
                    'format': p.get('format',''),
                    'creator': p.get('creator',''),
                    'creative_preview': p.get('creative_name','') or nm,
                    'theme': '', 'dimensions': '', 'primary_text': '', 'headline': '',
                    'description': '', 'cta': '', 'landing_page': '', 'creative_link': '',
                    'built': '', 'live_date': e.get('created',''),
                    'created': e.get('created',''), 'status': e.get('status',''),
                    'daily': {}, 'meta_ad_names': [], 'is_dpa': False,
                })
            print(f"  → {len(new_ads_cards)} new ads (last 7 days) loaded")
        except Exception as ex:
            print("  ! new_ads load failed:", ex)

    # Load logo
    print(f"Reading logo: {logo_path}")
    with open(logo_path, 'rb') as f:
        logo_b64 = base64.b64encode(f.read()).decode()
    logo_data_url = f"data:image/jpeg;base64,{logo_b64}"

    # Load template
    print(f"Reading template: {template_path}")
    with open(template_path) as f:
        html = f.read()

    # Substitute
    html = html.replace('__CARDS_JSON__', json.dumps(cards, default=list))
    html = html.replace('__META_ADS_JSON__', json.dumps(meta_ads_full, default=list))
    html = html.replace('__EARLIEST_DATE__', earliest)
    html = html.replace('__LATEST_DATE__', latest)
    html = html.replace('__ALL_CAMPAIGNS__', json.dumps(sorted(list(all_camps))))
    html = html.replace('__BAKED_MAPPING__', json.dumps(baked_mapping))
    html = html.replace('__NEW_ADS_JSON__', json.dumps(new_ads_cards, default=list))
    html = html.replace('__R2_BASE__', r2_base)
    html = html.replace('__ACCOUNT_DAILY__', json.dumps(account_daily))
    html = html.replace('__PLATFORM__', platform)
    # Audience-segment split (New / Engaged / Existing) - Meta page only (TikTok has no such metric)
    seg_daily, ad_seg = {}, {'window': ['', ''], 'ads': {}}
    if platform == 'meta':
        if segments_csv and os.path.exists(segments_csv):
            with open(segments_csv, encoding='utf-8-sig') as sf:
                for r in csv.DictReader(sf):
                    seg_daily.setdefault(r['Day'], {})[r['Segment']] = to_float(r.get('Spend'))
            print(f"  → {len(seg_daily)} audience-segment days (New/Engaged/Existing KPI)")
        if ad_segments and os.path.exists(ad_segments):
            ad_seg = json.load(open(ad_segments, encoding='utf-8'))
            print(f"  → {len(ad_seg.get('ads', {}))} ads with daily audience split")
    html = html.replace('__SEGMENT_DAILY__', json.dumps(seg_daily))
    html = html.replace('__AD_SEGMENTS__', json.dumps(ad_seg))
    html = html.replace('__LOGO_URL__', logo_data_url)


    # ---- platform nav (Meta | TikTok) + platform labelling ----
    def _tab(label, href, key):
        on = (key == platform)
        st = ("background:#5954a4;color:#fff;" if on else "background:transparent;color:#5954a4;")
        return ('<a href="%s" style="padding:7px 22px;border-radius:999px;font:600 13px/1 system-ui,-apple-system,sans-serif;'
                'text-decoration:none;%s">%s</a>' % (href, st, label))
    nav = ('<div id="platform-nav" style="display:flex;justify-content:center;gap:6px;padding:10px 16px;background:#fff;'
           'border-bottom:1px solid rgba(89,84,164,.15);position:sticky;top:0;z-index:1000;">'
           + _tab('Meta', 'index.html', 'meta') + _tab('TikTok', 'tiktok.html', 'tiktok') + '</div>')
    html = html.replace('<body>', '<body>' + nav, 1)
    if platform == 'tiktok':
        html = html.replace('Herology Performance</span>', 'Herology Performance · TikTok</span>', 1)
        html = html.replace('Every Meta ad with linked video', 'Every TikTok ad with linked video')
        html = html.replace('see it on Instagram with', 'see it on TikTok with')
        html = html.replace('<title>', '<title>TikTok · ', 1)
        _note = ('<div style="max-width:1100px;margin:24px auto;padding:12px 16px;font:12px/1.5 system-ui,sans-serif;color:#6b6b80;">'
                 'TikTok notes: Purchases = TikTok conversion-time purchases (Ads Manager default view). Purchase value and ROAS = purchases &times; account AOV for the period. '
                 'Video Views = video plays (comparable to the Views metric on the Meta tab); Hook Rate = 2-second views &divide; impressions (TikTok has no 3-second metric). Follows, Shares and Likes are TikTok engagement from ads (TikTok reports no saves). Hold Rate = 100% views &divide; 2-second views.</div>')
        html = html.replace('</body>', _note + '</body>', 1)
        html = html.replace('Meta Ad Name', 'TikTok Ad Name').replace('Aggregated Meta Ad Variants', 'Aggregated TikTok Ad Variants').replace("This Meta ad isn't", "This TikTok ad isn't")

    # Verify no placeholders left unfilled
    leftover = re.findall(r'__[A-Z_]+__', html)
    if leftover:
        print(f"⚠ WARNING: unfilled placeholders: {set(leftover)}")

    # Write
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n✓ Dashboard built: {output_path}")
    print(f"  File size: {os.path.getsize(output_path):,} bytes")

    return output_path


def main():
    p = argparse.ArgumentParser(description="Build the Herology Creative Hub dashboard")
    p.add_argument('--meta-csv', required=True, help="Meta Ads daily ad-level CSV export")
    p.add_argument('--hub-csv', required=True, help="Herology Creative Hub Ad Sheet CSV")
    p.add_argument('--output', required=True, help="Path for output HTML")
    p.add_argument('--logo', help="Brand logo PNG (default: bundled with skill)")
    p.add_argument('--mapping', help="Baked video mapping JSON (default: bundled)")
    p.add_argument('--template', help="HTML template (default: bundled)")
    p.add_argument('--r2-base', default=DEFAULT_R2_BASE,
                   help=f"R2 bucket public URL (default: {DEFAULT_R2_BASE})")
    p.add_argument('--account-csv', help="Account-level daily CSV (drives header KPIs). "
                   "Defaults to bundled account_master.csv if present.")
    p.add_argument('--new-ads', help='new_ads JSON path (default: new_ads.json next to script)')
    p.add_argument('--platform', default='meta', choices=['meta','tiktok'])
    p.add_argument('--segments-csv', default=None)
    p.add_argument('--ad-segments', default=None)
    args = p.parse_args()

    here = Path(__file__).parent
    template = args.template or str(here / 'dashboard_template.html')
    mapping = args.mapping or str(here / 'video_mapping.json')
    logo = args.logo or str(here / 'Herology_Logo.png')
    account = args.account_csv
    if account is None and (here / 'account_master.csv').exists():
        account = str(here / 'account_master.csv')

    build(args.meta_csv, args.hub_csv, args.output, template, mapping, logo, args.r2_base, account, args.new_ads, args.platform, args.segments_csv, args.ad_segments)


if __name__ == '__main__':
    main()
