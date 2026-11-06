#!/usr/bin/env python3
"""
api_to_csv.py  —  Convert Meta Ads MCP (ads_get_ad_entities) JSON responses
into the exact CSV format the Herology saved reports produce, so run_update.py
can consume them unchanged.

WHY THIS EXISTS
---------------
Instead of driving the browser to trigger + download the three CSV exports,
Claude pulls the same numbers directly from the Meta Ads connector and saves
each response as JSON. This script reshapes that JSON into the Meta report
CSV layout. Validated 2026-09-17: Views, spend, clicks, add-to-cart,
purchases, revenue, Hook Rate, Hold Rate, 100% Video View, shares, saves and
follows all reproduce the Meta export to 0.00%. (Account-level *reach* runs
~2-6% lower via the API — a Meta cross-campaign dedup quirk — so frequency/CTR
at account level shift slightly. Campaign/ad reach match closely.)

FIELD MAP (MCP field -> CSV column)
  reach                         -> Reach
  impressions / reach           -> Frequency
  amount_spent                  -> Amount spent (EUR)
  link_click                    -> Link clicks
  amount_spent / link_click     -> CPC (cost per link click)
  media_views                   -> Views
  omni_add_to_cart              -> Adds to cart
  omni_purchase                 -> Purchases
  omni_purchase_values          -> Purchases conversion value
  3_second_video_plays / imp    -> Hook Rate %        (stored as a fraction)
  thruplay / 3_second_video_plays-> Hold Rate         (stored as a fraction)
  thruplay / impressions        -> 100% Video View    (stored as a fraction)
  post_shares                   -> Post shares
  post_save                     -> Post saves
  instagram_profile_follow_v2   -> Instagram follows

AD-LEVEL 3-SECOND PLAYS (the workaround)
  `3_second_video_plays` is NOT exposed at ad level by the MCP (only
  ad_account / campaign / adset), and the dedicated `cost_per_video_view`
  field is rounded to whole cents (useless). BUT the `cost_per_action_type`
  breakdown returns `cost_per_action_type:video_view` UNROUNDED (~6 dp), where
  video_view IS the 3-second video play. So per ad:
      3_second_video_plays = amount_spent / (cost_per_action_type:video_view)
  Validated 2026-09-17 across multiple ads to ~6 dp. This script uses that
  automatically when the raw `3_second_video_plays` field is absent. So ALWAYS
  include `cost_per_action_type` in the ad-level pull.

USAGE
  Claude saves the MCP responses (the JSON array from `ad_entities`) to:
    _api_account.json   (level=ad_account, date_preset=last_30d, time_increment=1)
    _api_campaign.json  (level=campaign,   same range/increment, +campaign_name)
    _api_ad.json        (optional; level=ad, +campaign/adset/ad names)
  then:  python3 api_to_csv.py
  Writes Meta-format CSVs into the same folder, which run_update.py picks up.
"""
import json, glob, os, re, csv, sys

HERE = os.path.dirname(os.path.abspath(__file__))

def money(v):
    """'€1,321.21 EUR' -> 1321.21 ; None/'' -> None"""
    if v is None: return None
    s = re.sub(r'[^0-9.\-]', '', str(v))
    return float(s) if s not in ('', '-', '.') else None

def num(v):
    if v in (None, ''): return None
    try: return float(v)
    except (TypeError, ValueError): return None

def _extract(raw):
    if isinstance(raw, dict) and 'ad_entities' in raw:
        ents = raw['ad_entities']
        return json.loads(ents) if isinstance(ents, str) else ents
    return raw if isinstance(raw, list) else []

def load(prefix):
    """Merge every JSON page for a level: _api_account.json, _api_account_1.json, ...
    Each file is a bare list or the tool envelope {"ad_entities": "<json string>"}.
    Returns the concatenated list, or None if no file exists."""
    base = prefix[:-5] if prefix.endswith('.json') else prefix   # strip .json if given
    files = sorted(glob.glob(os.path.join(HERE, base + '*.json')))
    if not files: return None
    out = []
    for p in files:
        with open(p, encoding='utf-8') as f:
            out.extend(_extract(json.load(f)))
    return out

HEADER_BASE = ["Reach","Frequency","Amount spent (EUR)","Link clicks",
    "CPC (cost per link click)","Views","Adds to cart","Cost per add to cart",
    "Purchases","Cost per purchase","Purchases conversion value",
    "Purchase ROAS (return on ad spend)","CPMr (EUR)","100% Video View",
    "Hook Rate %","Hold Rate","Post shares","Post saves","Instagram follows",
    "Reporting starts","Reporting ends"]

def build_row(e):
    day   = e.get('date_start')
    imp   = num(e.get('impressions')) or 0
    reach = num(e.get('reach')) or 0
    spend = money(e.get('amount_spent'))
    lc    = num(e.get('link_click'))
    mv    = num(e.get('media_views'))
    atc   = num(e.get('omni_add_to_cart'))
    pur   = num(e.get('omni_purchase'))
    rev   = money(e.get('omni_purchase_values'))
    sec3  = num(e.get('3_second_video_plays'))
    if sec3 is None:
        # Ad-level workaround: 3-sec plays = spend / cost_per_action_type:video_view
        cpv = num(e.get('cost_per_action_type:video_view'))
        if cpv and spend is not None and cpv != 0:
            sec3 = spend / cpv
    thru  = num(e.get('video_thruplay_watched_actions'))
    sh    = num(e.get('post_shares'))
    sv    = num(e.get('post_save'))
    fol   = num(e.get('instagram_profile_follow_v2'))

    freq   = (imp/reach) if reach else None
    cpc    = (spend/lc) if (spend is not None and lc) else None
    costatc= (spend/atc) if (spend is not None and atc) else None
    cpp    = (spend/pur) if (spend is not None and pur) else None
    roas   = (rev/spend) if (rev is not None and spend) else None
    cpmr   = (spend*1000/reach) if (spend is not None and reach) else None
    vv100  = (thru/imp) if (thru is not None and imp) else None      # "100% Video View" col
    hook   = (sec3/imp) if (sec3 is not None and imp) else None      # Hook Rate % (fraction)
    hold   = (thru/sec3) if (thru is not None and sec3) else None    # Hold Rate  (fraction)

    vals = {
        "Day": day, "Reach": reach, "Frequency": freq,
        "Amount spent (EUR)": spend, "Link clicks": lc,
        "CPC (cost per link click)": cpc, "Views": mv, "Adds to cart": atc,
        "Cost per add to cart": costatc, "Purchases": pur,
        "Cost per purchase": cpp, "Purchases conversion value": rev,
        "Purchase ROAS (return on ad spend)": roas, "CPMr (EUR)": cpmr,
        "100% Video View": vv100, "Hook Rate %": hook, "Hold Rate": hold,
        "Post shares": sh, "Post saves": sv, "Instagram follows": fol,
        "Reporting starts": day, "Reporting ends": day,
    }
    return vals

def write_csv(path, entities, extra_cols):
    """extra_cols: list of (CSV column, MCP field) inserted after Day."""
    header = ["Day"] + [c for c, _ in extra_cols] + HEADER_BASE
    rows = []
    for e in entities:
        base = build_row(e)
        for col, fld in extra_cols:
            v = e.get(fld)
            # At CAMPAIGN level the MCP returns the campaign name in `name` and
            # leaves `campaign_name` null (at ad level `name` is the AD name and
            # campaign_name/adset_name are populated). Fall back to `name` only
            # when this entity has no ad-level naming, so campaign rows keep
            # their name instead of silently going blank.
            if not v and fld == 'campaign_name' and 'adset_name' not in e:
                v = e.get('name')
            base[col] = v
        rows.append(base)
    rows.sort(key=lambda r: (r["Day"], *[str(r.get(c, "")) for c, _ in extra_cols]))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in header})
    print(f"  wrote {os.path.basename(path)}: {len(rows)} rows")

def main():
    acc = load("_api_account")
    cam = load("_api_campaign")
    ad  = load("_api_ad")
    if not acc or not cam:
        sys.exit("Missing _api_account.json or _api_campaign.json in " + HERE)
    print("Transforming API JSON -> Meta-format CSVs:")
    write_csv(os.path.join(HERE, "API-Account-Level-Performance.csv"), acc, [])
    write_csv(os.path.join(HERE, "API-Campaign-Level-Performance.csv"), cam,
              [("Campaign name", "campaign_name")])
    if ad:
        write_csv(os.path.join(HERE, "API-Ad-Level-Performance.csv"), ad,
                  [("Campaign name", "campaign_name"),
                   ("Ad set name", "adset_name"),
                   ("Ad name", "name")])
        print("  ad-level Hook/Hold derived from cost_per_action_type:video_view.")
    print("Done. Now run: python3 run_update.py")

if __name__ == "__main__":
    main()
