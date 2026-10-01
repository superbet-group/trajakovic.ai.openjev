import re,json,sys
t=open('OPENJEV_MCP_SKILLS_SPEC.md').read()
blocks=[(m.start(),m.group(1),m.group(2)) for m in re.finditer(r'```(json\w*|jsonc)?\n(.*?)```',t,re.S) if m.group(1)]
out=[]
for i,(pos,lang,b) in enumerate(blocks):
    line=t[:pos].count('\n')+1
    try: j=json.loads(b); ok=True
    except Exception as e: j=None; ok=str(e)[:80]
    kind='req' if isinstance(j,dict) and 'questions' in j else ('resp' if isinstance(j,dict) and 'answers' in j else 'other')
    out.append(dict(i=i,line=line,lang=lang,ok=ok,kind=kind,keys=list(j)[:6] if isinstance(j,dict) else type(j).__name__,raw=b))
json.dump(out,open('review/blocks.json','w'))
for o in out: print(o['i'],o['line'],o['lang'],o['kind'],o['ok'] if o['ok'] is not True else '',o['keys'])
