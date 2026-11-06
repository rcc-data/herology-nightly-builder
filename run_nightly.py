#!/usr/bin/env python3
"""
run_nightly.py  —  One deterministic step for the nightly Herology routine.

Given the three Meta Ads API JSON responses already saved by Claude to Downloads
(_api_account.json, _api_campaign.json, _api_ad.json), this:

  1. Transforms them into Meta-format CSVs   (via api_to_csv.py in Downloads)
  2. Builds DASHBOARD 1 — Meta Performance    (run_update.py in Downloads)
       -> Herology_MetaPerformance_*.html + updates _dashboard_template.html
  3. Upserts the API ad + account CSVs into the Creative Hub masters
       (creative-hub-engine/master_meta.csv, account_master.csv)
  4. Builds DASHBOARD 2 — Creative Hub        (creative-hub-engine/build_dashboard.py)
       -> Meta Ads/index.html   (uses the current hub.csv already refreshed from Drive)
  5. Builds the TikTok page of the Creative Hub  (added 2026-10-14)
       needs Downloads/_tiktok_report.json = the TikTok report_integrated_get response saved by Claude
       (see NIGHTLY_ROUTINE.md Step 6). build_tiktok_csv.py -> master_tiktok.csv / account_tiktok.csv /
       new_ads_tiktok.json, then build_dashboard.py --platform tiktok -> Meta Ads/tiktok.html.
       Set env SKIP_TIKTOK=1 to run Meta-only (then DO NOT publish tiktok.html - it would be stale).

It does NOT push. Claude runs the two .command files on the Mac afterwards
(the sandbox has no GitHub network).

Run:  python3 "<Meta Ads>/run_nightly.py"
Paths are auto-located via the session mounts, so no arguments are needed.
"""
import glob, os, sys, subprocess, shutil, time

def find_mount(name):
    # Cloud runs (herology-nightly-builder repo) set HEROLOGY_DOWNLOADS / HEROLOGY_META_ADS;
    # Mac runs fall back to the session mounts exactly as before.
    env = {'Downloads': 'HEROLOGY_DOWNLOADS', 'Meta Ads': 'HEROLOGY_META_ADS'}.get(name)
    if env and os.environ.get(env):
        return os.environ[env]
    hits = glob.glob(f'/sessions/*/mnt/{name}')
    if not hits:
        sys.exit(f"FATAL: '{name}' folder is not connected to this session.")
    return hits[0]

DOWNLOADS = find_mount('Downloads')
META_ADS  = find_mount('Meta Ads')
ENGINE    = os.path.join(META_ADS, 'creative-hub-engine')

def run(cmd, cwd=None):
    print(f"\n$ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    print(r.stdout[-4000:])
    if r.returncode != 0:
        print(r.stderr[-4000:])
        sys.exit(f"FATAL: step failed ({cmd[1] if len(cmd)>1 else cmd})")
    return r.stdout

def newest(pattern):
    m = glob.glob(os.path.join(DOWNLOADS, pattern))
    if not m:
        sys.exit(f"FATAL: no file matches {pattern} in Downloads")
    return sorted(m, key=os.path.getmtime)[-1]

print("="*70)
print("HEROLOGY NIGHTLY — build both dashboards from API data")
print("="*70)
for f in ('_api_account.json', '_api_campaign.json', '_api_ad.json'):
    p = os.path.join(DOWNLOADS, f)
    if not os.path.exists(p):
        sys.exit(f"FATAL: {f} not found in Downloads — Claude must save the API pulls first.")

# TikTok pre-flight (fail BEFORE building anything, so we never publish a half-refreshed set)
SKIP_TIKTOK = os.environ.get('SKIP_TIKTOK') == '1'
TT_JSON = os.path.join(DOWNLOADS, '_tiktok_report.json')
if not SKIP_TIKTOK:
    if not os.path.exists(TT_JSON):
        sys.exit("FATAL: _tiktok_report.json not found in Downloads — Claude must run NIGHTLY_ROUTINE.md Step 6a "
                 "(TikTok pull) and save the response there first. (SKIP_TIKTOK=1 to run Meta-only.)")
    age_h = (time.time() - os.path.getmtime(TT_JSON)) / 3600
    if age_h > 12:
        sys.exit(f"FATAL: _tiktok_report.json is {age_h:.0f}h old — re-pull TikTok (Step 6a) so the page isn't stale.")

# 1. JSON -> Meta-format CSVs
run(['python3', os.path.join(DOWNLOADS, 'api_to_csv.py')])

# 1b. Audience segments (New/Engaged/Existing) -> segments_master.csv + ad_segments.json
run(['python3', os.path.join(META_ADS, 'segments_to_csv.py')])

# 2. DASHBOARD 1 — Meta Performance
run(['python3', os.path.join(DOWNLOADS, 'run_update.py')])
run(['python3', os.path.join(META_ADS, 'patch_seg.py')])

# 3. Upsert API CSVs into the Creative Hub masters
api_ad  = newest('API-Ad-Level-Performance.csv')
api_acc = newest('API-Account-Level-Performance.csv')
master  = os.path.join(ENGINE, 'master_meta.csv')
acct    = os.path.join(ENGINE, 'account_master.csv')
for p in (master, acct):
    try: os.chmod(p, 0o644)
    except OSError: pass
run(['python3', os.path.join(ENGINE, 'update_master.py'), master, api_ad])
run(['python3', os.path.join(ENGINE, 'update_master.py'), acct,  api_acc])

# 4. DASHBOARD 2 — Creative Hub
out = os.path.join(META_ADS, 'index.html')
run(['python3', os.path.join(ENGINE, 'build_dashboard.py'),
     '--meta-csv', master,
     '--hub-csv',  os.path.join(ENGINE, 'hub.csv'),
     '--account-csv', acct,
     '--logo',     os.path.join(ENGINE, 'Herology_Logo.png'),
     '--mapping',  os.path.join(ENGINE, 'video_mapping.json'),
     '--template', os.path.join(ENGINE, 'dashboard_template.html'),
     '--segments-csv', os.path.join(ENGINE, 'segments_master.csv'),
     '--ad-segments', os.path.join(ENGINE, 'ad_segments.json'),
     '--output',   out])

# 5. TikTok page
tt_out = os.path.join(META_ADS, 'tiktok.html')
if not SKIP_TIKTOK:
    run(['python3', os.path.join(ENGINE, 'build_tiktok_csv.py'), TT_JSON, ENGINE])
    run(['python3', os.path.join(ENGINE, 'build_dashboard.py'),
         '--meta-csv', os.path.join(ENGINE, 'master_tiktok.csv'),
         '--hub-csv',  os.path.join(ENGINE, 'hub.csv'),
         '--account-csv', os.path.join(ENGINE, 'account_tiktok.csv'),
         '--new-ads',  os.path.join(ENGINE, 'new_ads_tiktok.json'),
         '--logo',     os.path.join(ENGINE, 'Herology_Logo.png'),
         '--mapping',  os.path.join(ENGINE, 'video_mapping.json'),
         '--template', os.path.join(ENGINE, 'dashboard_template.html'),
         '--platform', 'tiktok',
         '--output',   tt_out])

print("\n" + "="*70)
print("BUILT BOTH DASHBOARDS:")
print(f"  1. Meta Performance -> {newest('Herology_MetaPerformance_*.html')}")
print(f"     (also updated Downloads/_dashboard_template.html)")
print(f"  2. Creative Hub     -> {out}")
print(f"     TikTok page      -> {tt_out}" + ("  (SKIPPED - stale, do not publish)" if SKIP_TIKTOK else ""))
print("\nNEXT: publish by running the two .command files on the Mac:")
print("  - Downloads/_push_to_github.command      (Meta Performance dashboard)")
print("  - Meta Ads/push_to_github.command         (Creative Hub: index.html + tiktok.html)")
print("="*70)
