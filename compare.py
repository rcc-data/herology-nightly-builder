#!/usr/bin/env python3
"""compare.py: shadow-mode check of a cloud build against the live published pages.

Usage: python3 compare.py <live-creative clone> <meta-ads clone>
Run from the repo root after run_nightly.py. Byte-identical pages are the best case; when the
Mac and the cloud pulled the API at different times the most recent days can legitimately differ
a little, so this also compares the numbers that matter: account daily spend / purchases / value
(Creative Hub ACCOUNT_DAILY) and Meta Performance account rows (const ACC), plus card counts.
Exit 0 = match (identical, or every day within tolerance). Exit 1 = real difference.
"""
import json, os, re, sys, glob, hashlib

ROOT = os.path.dirname(os.path.abspath(__file__))
live_creative, live_meta = sys.argv[1], sys.argv[2]
TOL = 0.005  # 0.5% per day, allows for late-arriving conversions between pull times

def sha(p): return hashlib.sha256(open(p, 'rb').read()).hexdigest()
def js_value(html, name):
    m = re.search(r'^\s*const ' + re.escape(name) + r'\s*=\s*(.+?);\s*$', html, re.M)
    if not m: return None
    try: return json.loads(m.group(1))
    except Exception: return None

problems, notes = [], []
def check_pair(label, cloud, live):
    if not os.path.exists(live):
        problems.append(f"{label}: live file missing"); return None, None
    if sha(cloud) == sha(live):
        notes.append(f"{label}: byte-identical"); return None, None
    return open(cloud, encoding='utf-8').read(), open(live, encoding='utf-8').read()

def cmp_daily(label, a, b, keys):
    if a is None or b is None:
        problems.append(f"{label}: could not read daily data"); return
    days = sorted(set(a) & set(b)); worst = (0, None, None)
    for d in days:
        for k in keys:
            x, y = float(a[d].get(k) or 0), float(b[d].get(k) or 0)
            rel = abs(x - y) / max(abs(y), 1e-9) if y else (0 if x == 0 else 1)
            if rel > worst[0]: worst = (rel, d, k)
    only = sorted(set(a) ^ set(b))
    msg = f"{label}: {len(days)} common days, worst diff {worst[0]*100:.2f}% ({worst[1]} {worst[2]})"
    if only: msg += f", days only on one side: {only[:5]}"
    (problems if worst[0] > TOL else notes).append(msg)

# Creative Hub pages
for page in ('index.html', 'tiktok.html'):
    c, l = check_pair(f"Creative Hub {page}", os.path.join(ROOT, page), os.path.join(live_creative, page))
    if c is None: continue
    cmp_daily(f"Creative Hub {page} ACCOUNT_DAILY", js_value(c, 'ACCOUNT_DAILY'), js_value(l, 'ACCOUNT_DAILY'),
              ('spend', 'purchases', 'value'))
    rc, rl = js_value(c, 'RAW_CARDS'), js_value(l, 'RAW_CARDS')
    if rc is not None and rl is not None:
        (notes if len(rc) == len(rl) else problems).append(f"Creative Hub {page}: cards cloud {len(rc)} vs live {len(rl)}")

# Meta Performance
mp = sorted(glob.glob(os.path.join(ROOT, 'meta-performance', 'Herology_MetaPerformance_*.html')), key=os.path.getmtime)
if not mp:
    problems.append("Meta Performance: no cloud build found")
else:
    c, l = check_pair("Meta Performance", mp[-1], os.path.join(live_meta, 'index.html'))
    if c is not None:
        ac, al = js_value(c, 'ACC'), js_value(l, 'ACC')
        if ac is None or al is None:
            problems.append("Meta Performance: could not read const ACC")
        else:
            notes.append(f"Meta Performance: ACC rows cloud {len(ac)} vs live {len(al)}")
            if len(ac) != len(al): problems.append("Meta Performance: ACC row count differs")

for n in notes: print("OK   ", n)
for p in problems: print("DIFF ", p)
print("MATCH" if not problems else "MISMATCH")
sys.exit(1 if problems else 0)
