# Herology Nightly Routine — build & publish both dashboards from the Meta API

> ## ⛔ PREFLIGHT — do these four things BEFORE the first tool call. No exceptions.
> 1. **Read this whole file.** It is the only authority on how the pull works.
> 2. **Read memory** (`memory_list`, then the Herology + Meta Ads project files).
>    Some memory notes predate this routine and describe a CSV-upload workflow that is
>    **obsolete** — Ryan does NOT upload CSVs. If memory and this file disagree, THIS FILE WINS,
>    and Ryan's live word beats both. Never tell Ryan his process is something he says it isn't.
> 3. **Do not invent parameters.** Use exactly the calls in Step 1. `limit`, `sort`, chunk sizes
>    and the window are all load-bearing and were derived the hard way (see the ⚠️ in Step 1).
> 4. **Two-strike rule.** If the pull doesn't reconcile after two attempts, STOP theorising and
>    go look at artifacts on disk (previous CSVs, `_api_raw_*.txt`, this file). Do not start a
>    third hypothesis. On 2026-09-20 five consecutive guesses burned hours of Ryan's usage.
>
> 5. **TikTok connector IS available — do not report it missing.** The `TikTok_for_Business` MCP server
> connects a few seconds AFTER the session starts, so an early ToolSearch finds nothing. Search
> `+TikTok_for_Business report` (ToolSearch waits for servers listed as "still connecting"); the tool is
> `mcp__TikTok_for_Business__report_integrated_get`. Only call it missing if it's absent after the server
> has finished connecting. (Ryan, 23 Oct 2026 — this false alarm blocked several nightly runs.)
>
> Cost discipline: this run should take ~10 API calls. If you're past 20, you're guessing — stop
> and tell Ryan what you've found rather than continuing to spend.

Runs every evening. Pulls the **last 14 days** from the Meta Ads connector, rebuilds
BOTH dashboards, and publishes them. (Since 2026-10-14 the Creative Hub also has a TikTok page — see **Step 6**.) No manual CSV export anywhere.

**Why 14 days (changed 2026-09-20, Ryan's call):** both dashboards read from upsert stores
(`master_meta.csv`, `account_master.csv`, and run_update.py's own store) that already hold full
history back to 2026-01-01. The nightly pull only needs to *refresh* recent days. 14 days
comfortably covers Meta's 7-day click attribution window, so late-maturing purchases still get
corrected, while cutting the row count ~3x — fewer calls, less truncation risk, less of Ryan's
usage burned. Older history is never re-pulled and never lost.

Dashboards:
1. **Meta Performance** — `run_update.py` → repo `rcc-data/herology-meta-ads` → https://rcc-data.github.io/herology-meta-ads/
2. **Creative Hub** — `creative-hub-engine/build_dashboard.py` → repo `rcc-data/herology-live-creative` → https://rcc-data.github.io/herology-live-creative/

Key constants:
- Meta ad account: **698133907424422** (Herology, EUR)
- Hub sheet (Google Drive, live master): file id **1emaVoUdKxx3QfjL6YJof_X8Kt4Qix0FxLiG2sta47Co** ("Herology Creative Hub")
- Downloads holds: `api_to_csv.py`, `run_update.py`, `_dashboard_template.html`, `_push_to_github.command`
- This folder holds: `run_nightly.py`, `push_to_github.command`, `gh_token.txt`, `creative-hub-engine/`,
  **`herology_nightly.command`** (the only thing you launch), `split_api_raw.py`, `reconcile.py`,
  and the superseded `watch_tool_results.command` / `fetch_tool_results.command`

Expected run shape (14-day window): **~9 API calls**, 1 Finder launch, 0 further clicks.
  1 TikTok report (Step 6a) · 1 account · 1 campaign · 1 enumerate · ~4 object_ids chunks · 1 Drive download

**Required connectors (check before the first call; if any is missing or unauthed, STOP and report which):**
Meta Ads (`ads_get_ad_entities`), **TikTok for Business** (`report_integrated_get`), Google Drive, computer-use (to
launch the helper), and both connected folders (Downloads, Meta Ads). Without TikTok, `run_nightly.py` hard-fails
at Step 3 and nothing publishes — do not "fix" that with `SKIP_TIKTOK=1` unless Ryan says so.

**Nightly order:** 0 helper + cleanup → 6a TikTok pull → 1 Meta pull → reconcile → 2 hub.csv → 3 `run_nightly.py`
→ 4 publish (`index.html` + `tiktok.html` + Meta Performance) → 5 report (incl. TikTok).

---

## Step 0 — launch the helper, then clear stale pages

**0a. FIRST — launch `Meta Ads/herology_nightly.command`** via computer-use (Finder: select it,
press ⌘O). It MUST be running before the first API call, because every large response spills to
a directory the sandbox cannot read, and this is what copies them across. If you start pulling
first, those pages are lost and you'll have to re-pull them. Details in Step 1b.

**0b.** Delete any previous `_api_account*.json`, `_api_campaign*.json`, `_api_ad*.json`,
`_api_raw_*.txt` from Downloads (so a short pull can't be polluted by yesterday's pages). Also delete yesterday's `_tiktok_report.json` (re-created in Step 6a).
Deleting may need `allow_cowork_file_delete` once — that's expected, not an error.

## Step 1 — pull the Meta API (ads_get_ad_entities)
Use a fresh 20-char `client_conversation_id` for the whole run.

**WINDOW — `date_preset: "last_14d"` on every call.** (14 not 10: `last_14d` is a real preset,
whereas 10 days would need a hand-built `time_range` and a date calculation that can go wrong.
Ryan's call, 2026-09-20 — "do 14 days if that's easier". It is.)

**ARCHIVED ADS — mandatory on every campaign- and ad-level call.** By default the connector
omits archived ads, which silently loses spend: on 2026-09-20 the unfiltered pull reached only
98.86% of account spend, and adding this filter took it to **100.00%** (48 archived ads, €250.64).
Include this `filtering` on the campaign pull, the ad enumeration, AND every object_ids chunk:

```json
[{"field": "effective_status", "operator": "IN",
  "value": ["ACTIVE","PAUSED","DELETED","ARCHIVED","IN_PROCESS","WITH_ISSUES",
            "CAMPAIGN_PAUSED","ADSET_PAUSED","AD_PAUSED","DISAPPROVED",
            "PENDING_REVIEW","PREAPPROVED","PENDING_BILLING_INFO"]}]
```

That is every enum value `effective_status` has — deliberately exhaustive so a new status can't
quietly start dropping ads. Account level takes no filter (it has no entities to filter).
Everything below also takes `time_increment: "1"` unless explicitly stated otherwise.
Save each response's `ad_entities` array to Downloads. If a level paginates (`next_cursor`),
save each page as `_api_<level>_1.json`, `_api_<level>_2.json`, … (any suffix works — `api_to_csv.py`
merges every `_api_<level>*.json`). Resend all params unchanged with the cursor.

**Account** — `level: "ad_account"` → `_api_account.json`. Fields:
`["impressions","reach","frequency","amount_spent","link_click","media_views","omni_add_to_cart","omni_purchase","omni_purchase_values","3_second_video_plays","video_thruplay_watched_actions","post_shares","post_save","instagram_profile_follow_v2"]`

**Campaign** — `level: "campaign"` → `_api_campaign*.json`. Same fields **plus** `"campaign_name"`.

**Ad** — ⚠️ READ THIS BEFORE CHANGING ANYTHING (rewritten 2026-09-20).
The old instruction ("use `limit` + paginate") SILENTLY TRUNCATES and must not be used.
`limit` is documented as a count of *entities* but with `time_increment:"1"` it caps *rows*
(1 ad × 30 days = 30 rows), so `limit:1000` ≈ 33 ads, and the default page is 200 rows ≈ 6 ads.
Worse, the entity-search cursor dies early: it returns an empty page and then drops the cursor,
so a truncated pull looks complete. Measured 2026-09-20: that path gave 184 ads / 82% of account
spend (as low as 32% on recent days). Dropping `limit`, filtering by campaign, and pulling
day-by-day all truncate too. **The only method that reconciles is two passes:**

1. **Enumerate** the spenders — ONE call, and note it has **no `time_increment`**:
   `level:"ad"`, `date_preset:"last_14d"`, `sort:"amount_spent_descending"`, `limit:300`,
   `fields:["amount_spent"]`, **plus the effective_status filter above**.
   Returns ads ranked by spend; take every row that HAS an `amount_spent` (the tail runs down to
   €0.01, then to ads with no spend at all — stop there). Comes back inline; write the ids to
   `Downloads/_ad_ids.txt`. Verified 2026-09-20: 254 spenders = **100.00%** of 14-day account spend.
   If it returns 300 rows (= `limit`) AND the last row still has meaningful spend, raise `limit`
   and re-run — you're on the cap.
2. **Fetch dailies by id** — `object_ids` skips the broken entity search and returns a single
   response with NO cursor. Chunk the ids so `ads × days` stays safely under `limit`.
   With a **14-day** window and `limit:1000`: **65 ids per call** (≤910 rows worst case) —
   about 4 calls for ~254 ads. Keep the effective_status filter on these calls too.
   Same field list as below, plus `time_increment:"1"` and `date_preset:"last_14d"`.
   ⚠️ object_ids ALSO truncates silently at the row cap and returns no cursor — never send a
   chunk big enough to hit `limit`. If a chunk returns exactly `limit` rows, it truncated.

**ALWAYS reconcile before building** — this is the gate, run it every night:
```
python3 "<Meta Ads>/reconcile.py"     # ad vs account, per day + total
```
Expect **~100% overall and >97% on every day** (archived ads included). Anything materially lower means the pull truncated
— fix it, don't publish. Reconcile against `_api_account.json` from THIS run; never benchmark
against an older CSV (on 2026-09-20 doing that sent the diagnosis down the wrong path for an hour).

Save the chunks as `_api_ad.json`, `_api_ad_2.json`, … (`run_nightly.py` checks for the exact
base names `_api_account.json` / `_api_campaign.json` / `_api_ad.json`; `api_to_csv.py` then
merges every `_api_<level>*.json`).

Fields:
`["campaign_name","adset_name","name","amount_spent","cost_per_action_type","impressions","reach","frequency","link_click","media_views","omni_add_to_cart","omni_purchase","omni_purchase_values","video_thruplay_watched_actions","post_save","instagram_profile_follow_v2"]`
NOTE: ad level has **no** `3_second_video_plays` — `cost_per_action_type` carries `cost_per_action_type:video_view`, and `api_to_csv.py` derives 3-sec plays = amount_spent ÷ that. Include `cost_per_action_type` or per-ad Hook/Hold will be blank.
(The old line here said "use `limit` + paginate" — that is exactly what truncated the 2026-09-20
run. Deleted deliberately. Use the two-pass method above and nothing else.)

## Step 1b — bridge the spilled pages onto disk  (added 2026-09-20)
Campaign and ad responses are far too big to come back inline, so the connector writes them to
`/var/folders/*/*/T/claude-hostloop-plugins/*/projects/session/*/tool-results/`. That directory is
outside every folder connected to the sandbox, and the files are single-line JSON so the Read tool
can't slice them either — Claude cannot get at them unaided. Bridge them:

1. `herology_nightly.command` (launched in Step 0a) copies every new spill file into Downloads as
   `_api_raw_<timestamp>.txt` within ~2s, automatically. There is no per-page click.
   It is the ONLY Finder interaction in the whole routine.
   (`watch_tool_results.command` / `fetch_tool_results.command` are older bridge-only helpers,
   superseded — don't use them.)
2. In the sandbox: `python3 "<Meta Ads>/split_api_raw.py" --only <ts1,ts2,...>`. Pass the exact
   spill timestamps from YOUR calls (each tool error message ends with the filename). Other Claude
   sessions write to the same directory — on 2026-09-20 two foreign pages were nearly merged into
   the build. Always whitelist; never split blind.
   It infers each page's level from the row shape (`adset_name` → ad; otherwise account if every
   `id` equals the ad-account id, else campaign — account and campaign have IDENTICAL keys, so id
   is the only reliable discriminator), then writes `_api_account.json` / `_api_campaign_N.json` /
   `_api_ad_N.json`.
3. Rename so `run_nightly.py`'s existence check passes: `_api_campaign_1.json` → `_api_campaign.json`
   and `_api_ad_1.json` → `_api_ad.json`. It checks those exact base names; `api_to_csv.py` then
   merges every `_api_<level>*.json`, so the remaining numbered chunks are picked up automatically.
4. Then run `reconcile.py` (Step 1 gate) BEFORE building.

Ryan authorised this bridge on 2026-09-20. `fetch_tool_results.command` only copies, from the
connector's own output directory into Downloads; it touches nothing else.

## Step 2 — refresh hub.csv from Google Drive
Download Drive file **1emaVoUdKxx3QfjL6YJof_X8Kt4Qix0FxLiG2sta47Co** as `text/csv`
and write it to `creative-hub-engine/hub.csv` (overwrite). This is the live creative sheet.

## Step 3 — build all pages (one command)  — **Step 6a (TikTok pull) must be done BEFORE this**
```
python3 "<Meta Ads>/run_nightly.py"
```
(Since 2026-10-14 `run_nightly.py` ALSO builds the TikTok page. It hard-fails up front if
`Downloads/_tiktok_report.json` is missing or >12h old — that file is saved in Step 6a. `SKIP_TIKTOK=1` runs
Meta-only; if you do that, never publish `tiktok.html`, it would be stale.)
It transforms the API JSON → CSVs, runs `run_update.py` (dashboard 1), upserts the ad + account
CSVs into `master_meta.csv` / `account_master.csv`, and runs `build_dashboard.py` (dashboard 2 →
`Meta Ads/index.html`). It stops on any error. Confirm it prints "BUILT BOTH DASHBOARDS".

## Step 4 — publish (no clicking; `herology_nightly.command` is already running)
Only after `reconcile.py` exits 0 and Step 3 printed "BUILT BOTH DASHBOARDS":

```bash
touch "<Downloads>/_PUBLISH_NOW.flag"          # triggers BOTH pushes
sleep 45
cat "<Downloads>/_PUBLISH_RESULT.txt"          # read the actual result
```

It runs `Downloads/_push_to_github.command` (Meta Performance) then
`Meta Ads/push_to_github.command` (Creative Hub), each using its own PAT, and writes their
combined output to `_PUBLISH_RESULT.txt`. Read that file — do NOT screenshot Terminal to find
out whether it worked. Both sections must contain `SUCCESS`; if either doesn't, report the
failure verbatim and do not claim it published.

When the run is finished: `touch "<Downloads>/_STOP.flag"` to close the helper cleanly.

## Step 5 — verify & report
Both builds should cover through yesterday/today. Report: last date in the data, Meta Performance
7-day hero (revenue/purchases/ROAS), Creative Hub creative count + matched ads, and both commit
results. Also report the TikTok page: window, ads with data, spend / purchases / ROAS (and that spend matches TikTok's account total).
Flag anything that failed instead of silently continuing.

### Notes
- Account-level **reach** from the API runs ~2–6% under the old report (Meta cross-campaign dedup);
  spend/purchases/revenue/ROAS/ATC/clicks/Hook/Hold are exact. Accepted.
- If the Meta Ads, TikTok for Business or Google Drive connector isn't authed, or the Mac isn't awake for Step 4,
  stop and report which step blocked rather than partially publishing.
- **Spill location (seen 2026-10-15):** in a cloud-hosted run, oversized Meta/TikTok/Drive responses spill to the
  CLOUD sandbox (`/root/.claude/projects/.../tool-results/*.txt`), NOT the Mac — the helper's bridge copies nothing
  for them. Read the spill file with cloud Bash, extract the `ad_entities` (or report) array, write it to a fresh file,
  `SendUserFile` it, then `device_commit_files` to the exact Downloads name (`_api_ad.json`, `_api_ad_2.json`, …,
  `_tiktok_report.json`). Drive's `download_file_content` returns the CSV **base64-encoded** — decode before
  writing `hub.csv`. Small responses (account, campaign, enumerate) come back inline; write them directly.

---

## Step 6 — TikTok tab (added 2026-10-14, Ryan's request) — 6a is done BEFORE Step 3; 6b/6c are now automatic inside `run_nightly.py`
The Creative Hub now has two pages that share one nav bar: **Meta** = `index.html`, **TikTok** = `tiktok.html`
(same repo `rcc-data/herology-live-creative`, same template, built with `--platform tiktok`).
TikTok ad account: **7464910785131659265** (TikTok for Business connector; tools are called via
`report_integrated_get` directly, other tools via `tool_execute`). Window: the **last 7 complete days**
(Ryan: "I don't need backdated data, just the last 7 days of ads") — end = yesterday, start = end − 6.

**6a. Pull** — ONE call, `report_integrated_get`:
`report_type:"BASIC"`, `data_level:"AUCTION_AD"`, `dimensions:["ad_id","stat_time_day"]`, `page_size:1000`,
`start_date`/`end_date` as above, `metrics`:
`["ad_name","campaign_name","adgroup_name","spend","impressions","reach","frequency","clicks","video_play_actions","video_watched_2s","video_watched_6s","video_views_p100","complete_payment","total_complete_payment_rate","real_time_conversion","onsite_on_web_cart","shares","follows","likes","comments","profile_visits"]`
- **Use `ad_id`, NOT `ad_id_v2`.** `ad_id` splits the multivariation Smart+ ads into the individual videos
  inside them (names like `Balance - Period Pain x Niamh Doherty_wYQgvydK.MOV`). `ad_id_v2` rolls them back into
  one "MultiVariation" ad and loses the per-video detail. Expect ~51 ads with spend, ~320 daily rows.
- ONE invalid metric name fails the whole call (error 40002 lists the bad ones). Do not "improve" the list.
  Known-INVALID names: `video_watched_3s`, `focused_view`, `saves`, `favorites`, `purchase_value`, `roas`,
  `real_time_complete_payment*`, `real_time_*_value`, `conversion_value`. There is **no 3-second view metric**
  and **no saves metric** on TikTok, and **no real-time revenue metric**.
- The response (~230KB) spills to a tool-results file in the CLOUD sandbox (path is in the tool message; cloud Bash can read it,
  the Mac cannot). **Save it as `Downloads/_tiktok_report.json`**: copy the spill file to `/mnt/user-data/outputs/_tiktok_report_<date>.json`
  (fresh name every run) and `device_commit_files` it to `/Users/ryanclarke/Downloads/_tiktok_report.json`.
  Delete any older `_tiktok_report.json` first if the commit is refused. `run_nightly.py` reads exactly that path.

**6b. Convert — automatic.** `run_nightly.py` runs `creative-hub-engine/build_tiktok_csv.py Downloads/_tiktok_report.json creative-hub-engine/`
→ `master_tiktok.csv`, `account_tiktok.csv`, `new_ads_tiktok.json` (no manual commit of CSVs any more).
Sanity gate: the script prints AOV / rows / ads / spend / purchases / value; spend must equal the TikTok account total for
the same window (`report_integrated_get`, `data_level:AUCTION_ADVERTISER`, `metrics:["spend"]`) — check this in Step 5.

Metric definitions baked into the script (and printed in the page footnote — keep them in sync):
- **Purchases** = `real_time_conversion` (conversion-time basis = what Ads Manager shows by default). The API-default
  `complete_payment` (impression-date basis) runs ~10% lower and is what TikTok's own revenue is attached to.
- **Purchase value / ROAS** = purchases × AOV, AOV = Σ`total_complete_payment_rate` ÷ Σ`complete_payment`
  (that field is misnamed — it is TOTAL PAYMENT VALUE). Ryan explicitly chose to show this larger number as
  the headline revenue and NOT label it "estimate" in the KPIs; compromise in place = small footnote at the
  bottom of the TikTok page. Do not remove the footnote. Per-ad value uses the account AOV (per-ad AOV is too noisy).
- **Views** = `video_play_actions` (plays ≈ impressions, same as Meta's "Views" ≈ impressions → cost/view is comparable).
  Do NOT use 2s views for Views — it made TikTok look 5× dearer than it is.
- **Hook Rate** = `video_watched_2s` ÷ impressions (closest available to Meta's 3s). **Hold Rate** = `video_views_p100` ÷ `video_watched_2s`.
- **Follows / Shares / Likes** come from TikTok and are relabelled by the template per platform ("TikTok Follows", "Likes"
  in the Saves slot because TikTok has no saves; Meta shows "IG Follows", "Saves").

**6c. Build — automatic** (this is the command `run_nightly.py` runs; shown for manual re-runs, from `creative-hub-engine/`):
```
python3 build_dashboard.py --meta-csv master_tiktok.csv --hub-csv hub.csv --account-csv account_tiktok.csv \
  --new-ads new_ads_tiktok.json --platform tiktok --output ../tiktok.html
```
Expect "kept ~51 cards with data". TikTok-only behaviours in `build_dashboard.py`: cards limited to ads WITH data
(Ryan: "hard to see what ads are actually live"); every TikTok ad that doesn't match a hub row (the multivariation /
legacy ones — ~20 of 51) is added as its own card with a **product emoji placeholder** (Balance 🌸, Collagen ✨,
Creatine 💪, Inositol 🧬, PPN 🤰, Zen 🌙, Meno 🍂) and the label "Multivariation ad – video not available"
(their videos aren't in the hub sheet or R2). Names are tidied (extension + trailing id stripped).

## Step 4 addendum — publish BOTH pages
`push_to_github.command` now pushes `index.html` AND `tiktok.html` (loop). If you ever push without the Terminal
helper (e.g. `device_bash`), the script's final `rm` is now `|| true`; or push the file directly with the GitHub
contents API using the PAT in `gh_token.txt` (GET sha, PUT base64). Pages caches ~1–10 min: tell Ryan to hard-refresh (⌘⇧R).

## Dashboard behaviour that is now baked into `dashboard_template.html` (don't regress — Ryan asked for each)
- **Default date range = Month to Date** (Ryan, 16 Oct 2026) on ALL dashboards: Creative Hub Meta + TikTok pages ("This month" preset active on load and after Reset) and Meta Performance (new MTD button, active by default on Overview/Campaigns/Creative; edits live in `dashboard_template.html` and Downloads/`_dashboard_template.html`; backups `.pre-mtd`). Last 7 days is still one click away. Don't regress.
- **TikTok window = month to date** (Ryan, 16 Oct 2026, supersedes "last 7 days"): in Step 6a use `start_date` = 1st of the current month, `end_date` = yesterday (if today is the 1st, use the previous full month; stat_time_day max range is 30 days), and add `filtering:[{"field_name":"ad_status","filter_type":"IN","filter_value":"[\"STATUS_ALL\"]"}]` so spend equals the TikTok account total. Check against `report_integrated_get` AUCTION_ADVERTISER `spend` for the same dates.
- **TikTok window = 1st of PREVIOUS month through yesterday** (Ryan, 16 Oct 2026 — needed so the KPI strip can show % change vs the previous period; with only MTD data every KPI showed "NEW L7"). `stat_time_day` allows max 30 days per call, so Step 6a is now TWO `report_integrated_get` calls (same params/metrics/STATUS_ALL filter): (1) previous month 1st → last day; (2) current month 1st → yesterday. Check each against the AUCTION_ADVERTISER `spend` total for the same dates. Merge both `data.list` arrays into ONE file shaped `{"code":0,"message":"OK","data":{"page_info":{"page":1,"page_size":N,"total_number":N,"total_page":1},"list":[...]}}` and save as `Downloads/_tiktok_report.json`. (If today is the 1st/2nd of a month, the current-month call may be empty/short — fine.) "Last month" preset has no prior month of its own, so it shows NEW — accepted.
- **TikTok exclusion:** ads whose name matches `depression trial` (Nikki creatine depression trial) are dropped in `build_tiktok_csv.py` (EXCLUDE regex) — Ryan doesn't want them in the TikTok hub. Their spend (EUR 2.56 so far) is therefore the expected gap vs the TikTok account total.
- **Status filter defaults to "Live"** on both tabs (also after "Reset filters").
- **Month views:** "This month", "Last month" and a month picker next to the date presets.
- **Video loading:** videos are loaded lazily in batches (6 concurrent, 1400px ahead of scroll, hover jumps the queue).
  The old behaviour loaded all ~280 cards' videos at once and froze the page on wide date ranges. Never go back to eager loading.
- **KPI strip:** ONE row of 9 equal columns in one white card: Spend · Purchases · ROAS · Video Views · Cost/View ·
  Link Clicks (traffic) · Cost/Click · Follows · Saves (Meta) / Likes (TikTok), each with its change vs previous
  period. Top-level numbers come from `ACCOUNT_DAILY` (account CSVs carry Link clicks, Views, Post saves/shares/likes,
  Instagram follows). Ryan rejected two earlier layouts (stacked second row; grouped cards) — keep the single clean row.
- **Per-ad detail:** modal shows Video Views, Cost/View, Traffic (Link Clicks), Cost/Click, Shares, Saves/Likes, Follows;
  card metric toggle has Views·Cost/View, Clicks·CPC, Follows·Social; compare mode and the Data Table (+CSV export)
  carry the same fields; Data Table Hook Rate sort fixed.
- Number format: ≥1M shows as `1.09M`.

### Template/engine file map (all in `creative-hub-engine/`; `.pre-*` files are backups of earlier states)
`dashboard_template.html` · `build_dashboard.py` (`--platform meta|tiktok`, `--new-ads`) · `build_tiktok_csv.py` ·
`master_tiktok.csv` / `account_tiktok.csv` / `new_ads_tiktok.json` · `../tiktok.html` · `../index.html`.
**Meta-tab data note:** shares are 0 in the Meta CSV (API doesn't return them); Saves and IG follows do come through.
**Automation status (2026-10-15):** `run_nightly.py` now builds `tiktok.html`. Only the TikTok PULL (6a) and saving it to
Downloads is done by Claude, because it needs the TikTok MCP connector. Publishing (Step 4) pushes `index.html` + `tiktok.html`.

## Step 1c — Audience split (New / Existing) pulls  [added 2026-10-22]
Dashboards show spend split New vs Existing (Engaged is folded into New). Two extra Meta pulls, saved to Downloads as:
- `_seg_account.json`: ads_get_ad_entities level=ad_account, time_range 2026-01-01→yesterday, time_increment=1, breakdowns=["user_segment_key"], fields amount_spent, omni_purchase, omni_purchase_values, limit 2000 (spills to file → extract, SendUserFile, device_commit_files).
- `_seg_adday.json` (per-creative split that follows the report date range): ads_get_ad_entities level=ad, time_range = last 14 days (since..yesterday), time_increment="1", breakdowns=["user_segment_key"], fields id, name, amount_spent, limit 1000, object_ids chunks of ~10 spending ad ids (a response caps at ~1000 rows and silently drops the later segments, so keep chunks small; do NOT use the unfiltered paged call - it drops segments). Run chunks in parallel, they spill to files; merge in the cloud into a list of [day, ad_name, segment_key, spend] rows (dedupe on day+id+segment), SendUserFile + device_commit_files to Downloads/_seg_adday.json. Days re-pulled replace stored days (ad_segments.json accumulates history). A chunk that returns inline (small) cannot be saved - add overlapping ids so it spills.
NEVER name these `_api_ad*` — api_to_csv.py globs that prefix. run_nightly.py runs segments_to_csv.py + patch_seg.py automatically.
## TikTok pagination note
report_integrated_get caps at 1000 rows/page; a month can exceed it (Oct 2026: 1167). Fetch page 2 and merge; check spend ties to account totals.

## 2026-11-06 changes (Ryan) — these OVERRIDE anything above that conflicts
- **Never skip `_seg_adday.json` (per-creative New/Existing split).** Ryan: "Dont skip the new existing splits… find a more efficient way."
  Efficient method (~10 calls, not 22): take the ads with spend from the Step 1 ad pull, count each ad's days with spend,
  estimate rows = spend-days × 4 (measured ~3.2–3.9 segment rows per ad-day), and greedily pack ids (biggest first) into
  chunks of ≤900 estimated rows. Call: level=ad, date_preset last_14d, time_increment "1", breakdowns ["user_segment_key"],
  fields [id,name,amount_spent], limit 1000, effective_status filter, object_ids=<chunk>. **Gate:** each chunk's spend must
  equal the same ids' spend in `_api_ad*.json` and every day must tie to the account; a chunk returning ≥1000 rows truncated → split it.
  Merge into [day, ad_name, segment_key, spend] (dedupe day+id+segment) → `Downloads/_seg_adday.json`. Also refresh `_seg_account.json` (1 call) every run.
- **Run spilling calls ONE AT A TIME.** Two parallel calls spilled to the same millisecond filename and one overwrote the other.
- **TikTok previous month with 31 days:** `stat_time_day` is max 30 days per call → split it (e.g. 1–15 and 16–31) and page past 1000 rows.
- **Publish without Finder:** the Mac VM reaches api.github.com, so in cloud runs skip the `herology_nightly.command` launch and run
  `bash Downloads/_push_to_github.command </dev/null` and `bash "Meta Ads/push_to_github.command" </dev/null` from device_bash; both must print SUCCESS.
  (The GitHub MCP connector is not viable: it needs the file content passed inline, and the pages are 2–18 MB.)
  The Creative Hub script leaves a `.payload.*.json` in Meta Ads when deletion isn't granted. Harmless, or request delete for that folder.

## 2026-11-07 — HOW THE CLOUD RUN PUBLISHES (overrides every publish step above)
The scheduled cloud task can push ONLY to this repo (scheduled sessions get a fixed repo scope and no add_repo;
that caused the 403s on 6 and 7 Nov). So it never pushes to the live Pages repos:
1. `bash publish.sh --dist` force-pushes the built pages, as one commit with no history, to branch **`dist`** here:
   `live-creative/{index.html,tiktok.html,MANIFEST.json}` and `meta-ads/{index.html,MANIFEST.json}`.
2. `.github/workflows/pull-nightly.yml` in **rcc-data/herology-live-creative** and **rcc-data/herology-meta-ads**
   runs at :07/:27/:47 every hour, clones `dist` (public, no token), refuses files <200 KB or missing `</html>`,
   commits only if the pages changed, and pushes with the repo's own GITHUB_TOKEN. Pages redeploys from that push.
3. Backstop: Ryan's chat runs a 9:45pm PT check (send_later chain "Herology 9:45pm dashboard check"); if dist or the
   live repos didn't update, it publishes from the chat (which has push access to all three repos).
`publish.sh --live` still works from an interactive chat that has added the live repos with push access.
