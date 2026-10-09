import json,sys; sys.path.insert(0,'/home/jayakumaronline/vision-zero-work/wm3'); from metrics import flags
d=sys.argv[1]; p=json.load(open('grade_plan.json'))[d]
r2=json.load(open(f'{d}/prompt_bomb_v2.txt.results.json')); r1=json.load(open(f'{d}/prompt_bomb_v1.txt.results.json'))
for i in p['grade']:
  a=flags(r2['seg_%03d.mp4'%i]); b=flags(r1['seg_%03d.mp4'%i])
  print(f"{i:3d} v2 R{int(a[0])}I{int(a[1])} v1 R{int(b[0])}I{int(b[1])} | {a[2][:110]}")
