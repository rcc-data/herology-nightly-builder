import csv, sys, os
KEY=['Day','Campaign name','Ad set name','Ad name']
def load(p):
    with open(p,encoding='utf-8-sig') as f:
        r=csv.DictReader(f); return r.fieldnames, list(r)
master_path,new_path=sys.argv[1],sys.argv[2]
if os.path.exists(master_path): fields,master=load(master_path)
else: fields,master=None,[]
nf,new=load(new_path)
# union fields, master order first
fields=fields or nf
for c in nf:
    if c not in fields: fields.append(c)
k=lambda r: tuple(r.get(c,'') for c in KEY)
idx={k(r):i for i,r in enumerate(master)}
add=upd=0
for row in new:
    kk=k(row)
    if kk in idx: master[idx[kk]]=row; upd+=1
    else: master.append(row); idx[kk]=len(master)-1; add+=1
with open(master_path,'w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
    for r in master: w.writerow({c:r.get(c,'') for c in fields})
days=sorted(set(r.get('Day','') for r in master if r.get('Day')))
print(f"merged {os.path.basename(new_path)}: +{add} new rows, {upd} updated | total {len(master)} rows, {len(days)} days, {days[0]} -> {days[-1]}")
