import json,sys
for f in sys.argv[1:]:
    d=json.load(open(f))
    print('=====',f)
    for r in d['runs']:
        for i,t in enumerate(r['turns']):
            m=t.get('metrics',{})
            jg=m.get('judge_grounded',{}); gf=m.get('grounded_facts',{})
            bad=[k for k,v in (('JG',jg),('GF',gf)) if v and v.get('passed') is False]
            if not bad: continue
            print(f"--- {r['case']}/{i+1} r{r['repeat']} {bad} tools={[x['name'] for x in t['tools']]}")
            if 'JG' in bad: print('  JG:', jg.get('reasoning'),'| claims:', (jg.get('metadata') or {}).get('unsupported_claims'))
            if 'GF' in bad: print('  GF:', gf.get('reasoning'), json.dumps(gf.get('metadata'),ensure_ascii=False)[:300])
