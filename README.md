# Herology nightly builder

Cloud build of the three Herology dashboards. Replaces the Mac-based nightly run (Finder helper, Downloads flag files, push scripts with saved tokens).

| Page | Built by | Published to |
|---|---|---|
| Meta Performance | `meta-performance/run_update.py` | rcc-data/herology-meta-ads |
| Creative Hub (Meta) | `creative-hub-engine/build_dashboard.py` | rcc-data/herology-live-creative `index.html` |
| Creative Hub (TikTok) | `build_dashboard.py --platform tiktok` | rcc-data/herology-live-creative `tiktok.html` |

## Layout
- `meta-performance/` plays the role of the Mac's Downloads folder. Nightly API pulls are written here (`_api_*.json`, `_seg_*.json`, `_tiktok_report.json`; gitignored).
- Repo root plays the role of the Mac's `Meta Ads` folder: `run_nightly.py`, `reconcile.py`, `segments_to_csv.py`, `patch_seg.py`, `split_api_raw.py`, `NIGHTLY_ROUTINE.md` (pull rules: still authoritative for every API call).
- **State stores are committed and are the database. Do not delete or regenerate them:**
  - `meta-performance/_dashboard_template.html`: Meta Performance UI shell AND full history (`const ACC/CAM/AD`).
  - `creative-hub-engine/master_meta.csv`, `account_master.csv`, `segments_master.csv`, `ad_segments.json`: Creative Hub history (upserted nightly).
  - `creative-hub-engine/video_mapping.json`: hub ad name → exact Cloudflare R2 filename. Ads without an entry fall back to `<ad name>.mp4/.MP4/.mov/.MOV/...`; images and old human-named files need an entry.

## Run (cloud)
```bash
export HEROLOGY_DOWNLOADS=$PWD/meta-performance HEROLOGY_META_ADS=$PWD
# 1. write the pulls into meta-performance/ (see NIGHTLY_ROUTINE.md), hub sheet CSV to creative-hub-engine/hub.csv
python3 reconcile.py            # gate: must exit 0
python3 run_nightly.py          # must print BUILT BOTH DASHBOARDS
bash publish.sh                 # shadow: compare vs live, push nothing
bash publish.sh --live          # publish both repos
# 2. commit the updated stores back to this repo
```
The scripts still run unchanged on the Mac: with the env vars unset they find the Downloads / Meta Ads mounts as before.

## Verified
5 Nov 2026: tonight's inputs run through this repo in the cloud sandbox produced byte-identical `index.html`, `tiktok.html`, `Herology_MetaPerformance_Nov52026.html`, `_dashboard_template.html`, `master_meta.csv` and `ad_segments.json` to the Mac's published versions.

No secrets live here. Cloudflare R2 keys are not needed (pages load media from the public r2.dev URL in the viewer's browser).
