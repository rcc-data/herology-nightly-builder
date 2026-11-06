#!/usr/bin/env python3
"""Inject `const SEG = {...};` (day -> {new,engaged,existing,unknown} spend) into Meta Performance
_dashboard_template.html and the latest dated Herology_MetaPerformance_*.html. Run after run_update.py."""
import csv, glob, json, os, re, sys
here=os.path.dirname(os.path.abspath(__file__))
D=os.environ.get('HEROLOGY_DOWNLOADS') or (os.path.expanduser('~/mnt/Downloads') if os.path.isdir(os.path.expanduser('~/mnt/Downloads')) else os.path.join(here,'..','..','..','Downloads'))
csvp=os.path.join(here,'creative-hub-engine','segments_master.csv')
seg={}
for r in csv.DictReader(open(csvp)):
    seg.setdefault(r['Day'],{})[r['Segment']]=round(float(r['Spend'] or 0),2)
line='const SEG = '+json.dumps(seg,separators=(',',':'))+';'
targets=[os.path.join(D,'_dashboard_template.html')]
dated=sorted(glob.glob(os.path.join(D,'Herology_MetaPerformance_*.html')),key=os.path.getmtime)
if dated: targets.append(dated[-1])
for t in targets:
    s=open(t,encoding='utf-8').read().split('\n')
    idx=[i for i,l in enumerate(s) if l.startswith('const SEG ')]
    if not idx: sys.exit(f'FATAL: no SEG placeholder in {t}')
    s[idx[0]]=line
    open(t,'w',encoding='utf-8').write('\n'.join(s)); print('patched',t)
