import json, subprocess, os
F="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
plan=json.load(open('grade_plan.json'))
for d,p in plan.items():
    os.makedirs(f'{d}/rows',exist_ok=True)
    for i in p['grade']:
        out=f'{d}/rows/row_{i:03d}.jpg'
        if os.path.exists(out): continue
        tag=('A' if i in p['v2flag'] else '-')
        subprocess.run(['ffmpeg','-v','error','-y','-i',f'{d}/seg_{i:03d}.mp4','-vf',
          f"fps=1,scale=240:-2,pad=iw:180:0:(oh-ih)/2,tile=5x1,pad=iw+70:ih:70:0,drawtext=fontfile={F}:text='{i}':x=6:y=70:fontsize=28:fontcolor=yellow",
          '-frames:v','1',out],check=True)
    rows=[f'{d}/rows/row_{i:03d}.jpg' for i in p['grade']]
    for s in range(0,len(rows),8):
        ch=rows[s:s+8]; out=f'{d}/sheet_{s//8:02d}.jpg'
        args=sum([['-i',r] for r in ch],[])
        subprocess.run(['ffmpeg','-v','error','-y',*args,'-filter_complex',f"{''.join(f'[{k}:v]' for k in range(len(ch)))}vstack=inputs={len(ch)}" if len(ch)>1 else 'null',out],check=True)
    print(d, len(rows))
