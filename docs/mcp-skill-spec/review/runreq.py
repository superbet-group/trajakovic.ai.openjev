import json,urllib.request,sys
b=json.load(open('review/blocks.json'))
def post(r):
    q=urllib.request.Request('http://127.0.0.1:8080/v1/systemone',json.dumps(r).encode(),{'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(q,timeout=300) as x: return x.status,json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:300]
for ri,pi in [(5,6),(8,9),(10,11),(15,16),(21,22),(26,27),(32,33),(38,39)]:
    req=json.loads(b[ri]['raw']); shown=json.loads(b[pi]['raw'])
    s,r=post(req)
    print('##',ri,'line',b[ri]['line'],s)
    if s!=200: print(r); continue
    for k,v in r['answers'].items():
        sv=shown['answers'][k]
        f=lambda a:{x:(round(a[x],4) if isinstance(a[x],float) else a[x]) for x in ('noul','choice','score') if x in a}
        print(' ',k,'live',f(v),'shown',f(sv), 'usage',r.get('usage'),shown.get('usage'))
