import json,base64,urllib.request
def post(r):
    q=urllib.request.Request('http://127.0.0.1:8080/v1/systemone',json.dumps(r).encode(),{'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(q,timeout=300) as x: return x.status,json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code,e.read().decode()[:300]
R='/Users/trajakovic/Projects/Models/openjev/'
im=base64.b64encode(open(R+'tests/data/hotdog.jpg','rb').read()).decode()
s,r=post({"model":"openjev-latest","state":"Look at the photo.","images":["data:image/jpeg;base64,"+im],"questions":{"hotdog":{"type":"noul","instructions":"The photo shows a hot dog"},"cat":{"type":"noul","instructions":"The photo shows a cat"}}})
print('image',s,{k:v.get('noul') for k,v in r['answers'].items()} if s==200 else r, r.get('usage'))
b=json.load(open('review/blocks.json'))
args=json.loads(b[37]['raw'])['arguments']
for it in args['items']:
    s,r=post({"model":"openjev-latest","samples":1,"state":it['state'],"questions":args['questions']})
    print('batch',it['id'],s,{k:(v.get('choice') or round(v['noul'],4)) for k,v in r['answers'].items()})
