#!/usr/bin/env python3
"""Convert a TikTok report_integrated_get JSON (ad-level, daily) into the Meta-schema CSVs
that build_dashboard.py --platform tiktok expects.

Usage: python3 build_tiktok_csv.py <tiktok_report.json> [out_dir]
Writes: master_tiktok.csv, account_tiktok.csv, new_ads_tiktok.json  (into out_dir, default = this folder)

Metric mapping (see NIGHTLY_ROUTINE.md Step 6):
  Views              = video_play_actions   (comparable to Meta's "Views", which ~= impressions)
  Hook Rate %        = video_watched_2s / impressions   (TikTok has NO 3-second metric)
  Hold Rate          = video_views_p100 / video_watched_2s
  Purchases          = real_time_conversion (conversion-time basis = Ads Manager default view)
  Purchase value     = purchases x AOV, AOV = SUM(total_complete_payment_rate) / SUM(complete_payment)
                       (total_complete_payment_rate is really TOTAL PAYMENT VALUE; TikTok has no real-time revenue metric)
  Follows / Shares / Likes -> 'Instagram follows' / 'Post shares' / 'Post likes' columns (template relabels per platform)
"""
import json, re, csv, sys
from collections import defaultdict
src = sys.argv[1]
out = (sys.argv[2] if len(sys.argv) > 2 else '.').rstrip('/') + '/'
j = json.load(open(src))
assert j['data']['page_info']['total_page'] == 1, 'more than one page - raise page_size or loop pages'
L = j['data']['list']
def f(x):
    try: return float(x)
    except: return 0.0
clean = lambda n: re.sub(r'^\d{15,}_', '', n)
tot_rev = sum(f(r['metrics']['total_complete_payment_rate']) for r in L)
tot_cp = sum(f(r['metrics']['complete_payment']) for r in L)
AOV = tot_rev / tot_cp if tot_cp else 0
cols = ['Day','Campaign name','Ad set name','Ad name','Reach','Frequency','Amount spent (EUR)','Link clicks','CPC (cost per link click)','Views','Adds to cart','Cost per add to cart','Purchases','Cost per purchase','Purchases conversion value','Purchase ROAS (return on ad spend)','CPMr (EUR)','100% Video View','Hook Rate %','Hold Rate','Post shares','Post saves','Post likes','Instagram follows','Reporting starts','Reporting ends','Ads']
EXCLUDE = re.compile(r'depression\s*trial', re.I)
excluded = defaultdict(float)
rows = []; newn = {}
day = defaultdict(lambda: [0, 0, 0]); ext = defaultdict(lambda: defaultdict(float))
for r in L:
    m = r['metrics']; d = r['dimensions']['stat_time_day'][:10]
    sp = f(m['spend']); imp = f(m['impressions'])
    if sp == 0 and imp == 0: continue
    if EXCLUDE.search(m['ad_name']): excluded[m['ad_name']] += sp; continue   # Ryan 16 Oct 2026: keep Nikki creatine depression trial ads out of the TikTok hub
    cl = f(m['clicks']); pu = f(m['real_time_conversion']); val = pu * AOV
    v2 = f(m['video_watched_2s']); plays = f(m['video_play_actions']); p100 = f(m['video_views_p100']); atc = f(m['onsite_on_web_cart'])
    name = clean(m['ad_name'])
    if re.match(r'^\d{15,}_', m['ad_name']): newn[name] = '2026-10-08'
    e = ext[d]; e['clicks'] += cl; e['views'] += plays; e['shares'] += f(m['shares']); e['likes'] += f(m['likes']); e['follows'] += f(m['follows']); e['reach'] += f(m['reach']); e['atc'] += atc
    x = day[d]; x[0] += sp; x[1] += pu; x[2] += val
    rows.append({'Day': d, 'Campaign name': m['campaign_name'], 'Ad set name': m['adgroup_name'], 'Ad name': name, 'Reach': m['reach'], 'Frequency': m['frequency'],
        'Amount spent (EUR)': sp, 'Link clicks': cl, 'CPC (cost per link click)': sp/cl if cl else '', 'Views': plays, 'Adds to cart': atc, 'Cost per add to cart': '',
        'Purchases': pu, 'Cost per purchase': sp/pu if pu else '', 'Purchases conversion value': val, 'Purchase ROAS (return on ad spend)': val/sp if sp else '',
        'CPMr (EUR)': sp/imp*1000 if imp else '', '100% Video View': p100/imp if imp else 0, 'Hook Rate %': v2/imp if imp else 0, 'Hold Rate': p100/v2 if v2 else '',
        'Post shares': m['shares'], 'Post saves': '', 'Post likes': m['likes'], 'Instagram follows': m['follows'], 'Reporting starts': d, 'Reporting ends': d, 'Ads': ''})
with open(out + 'master_tiktok.csv', 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
with open(out + 'account_tiktok.csv', 'w', newline='', encoding='utf-8') as fh:
    a = csv.writer(fh)
    a.writerow(['Day','Amount spent (EUR)','Purchases','Purchases conversion value','Link clicks','Views','Post shares','Post saves','Post likes','Instagram follows','Reach','Adds to cart'])
    for d in sorted(day):
        a.writerow([d, round(day[d][0], 2), day[d][1], round(day[d][2], 2), ext[d]['clicks'], ext[d]['views'], ext[d]['shares'], '', ext[d]['likes'], ext[d]['follows'], ext[d]['reach'], ext[d]['atc']])
json.dump([{'name': n, 'created': d, 'status': 'ENABLE'} for n, d in sorted(newn.items())], open(out + 'new_ads_tiktok.json', 'w'))
print('AOV', round(AOV, 2), '| rows', len(rows), '| ads', len({r['Ad name'] for r in rows}), '| spend', round(sum(x[0] for x in day.values()), 2),
      '| purchases', sum(x[1] for x in day.values()), '| value', round(sum(x[2] for x in day.values()), 2), '| new ads', len(newn))
if excluded: print('EXCLUDED (Nikki creatine depression trial):', {k[:60]: round(v,2) for k,v in excluded.items()})
