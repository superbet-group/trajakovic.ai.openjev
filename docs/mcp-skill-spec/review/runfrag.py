import json,base64,urllib.request
def post(r):
    q=urllib.request.Request('http://127.0.0.1:8080/v1/systemone',json.dumps(r).encode(),{'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(q,timeout=300) as x: return x.status,json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:300]
b=json.load(open('review/blocks.json'))
S={43:"Subject: cancel\nI am furious, the invoice is wrong again. I will cancel today.",
44:"Subject: Charged twice\nBilled $49 twice, please refund. Cancel otherwise.",
45:"ERROR nightly job aborted: disk full",
46:"NEW ISSUE: CSV export loses rows when a quoted field contains a comma",
53:"def f():\n try:\n  x()\n except Exception:\n  pass\n",
62:"Please call me back on 415-555-0142. My office is 415-555-0199.",
64:"Goal: buy size M shirt\ne03 search\ne04 sign in\ne06 size dropdown\ne07 add to cart\ne08 wishlist\ne10 privacy",
66:"Goal: pay. Element: button 'Pay now'. Page: order confirmation.",
67:"def backoff(n): return min(2**n,60)",
}
for i,st in S.items():
    q=json.loads(b[i]['raw'])
    s,r=post({"model":"openjev-latest","state":st,"questions":q})
    print(i,b[i]['line'],s,{k:(v.get('choice') or v.get('score') or round(v['noul'],3)) for k,v in r['answers'].items()} if s==200 else r)
q={}
for k in (53,):
    pass
