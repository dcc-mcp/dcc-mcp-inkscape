"""Compose actual Inkscape PNGs. This is an assembly film, not a screen recording."""
import argparse
import json
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--width',type=int,default=1280);a=p.parse_args()
a.root.mkdir(exist_ok=True,parents=True)
w=a.width;h=w*9//16;fps=24;duration=12
font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
small=ImageFont.truetype(font,max(9,w//100));large=ImageFont.truetype(font,w//32)
images=[[Image.open(a.root/'artwork'/f'resonance-{i}-{s:02d}.png').convert('RGB') for s in [1,2,3]] for i in [1,2,3]]
frames=a.root/'video-frames';frames.mkdir(exist_ok=True)
def smooth(v):
 v=max(0,min(1,v));return v*v*(3-2*v)
for f in range(fps*duration):
 t=f/fps
 canvas=Image.new('RGB',(w,h),'#0b121c');draw=ImageDraw.Draw(canvas)
 draw.text((w*.045,h*.045),'RESONANCE ATLAS',font=large,fill='#f0eadc')
 draw.text((w*.045,h*.92),'INKSCAPE NATIVE VECTORS  /  ASSEMBLY FILM  /  NOT A SCREEN RECORDING',font=small,fill='#aab7c3')
 if t<8:
  idx=min(2,int(t/2.6667));local=t-idx*2.6667
  blend1=smooth((local-.2)/.75);blend2=smooth((local-1.15)/.6)
  im=Image.blend(images[idx][0],images[idx][1],blend1)
  im=Image.blend(im,images[idx][2],blend2)
  ph=int(h*.72*(1+.025*smooth(local/2.6)));pw=ph*3//4
  im=im.resize((pw,ph),Image.Resampling.LANCZOS)
  x=int(w*.54-pw/2);y=int(h*.52-ph/2)
  canvas.paste(im,(x,y))
  draw=ImageDraw.Draw(canvas)
  labels=['01  PHASE / LOCKED','02  BETWEEN / WAVES','03  AFTER / SOUND']
  draw.text((w*.045,h*.42),labels[idx],font=small,fill='#d2ff60')
  for j,word in enumerate(['MEASUREMENT GRID','SIGNAL GEOMETRY','EDITORIAL TYPE']):
   active=local>[0,.2,1.15][j]
   draw.text((w*.045,h*(.49+j*.04)),word,font=small,fill='#f0eadc' if active else '#465463')
 else:
  ph=int(h*.7);pw=ph*3//4;gap=int(w*.025);total=3*pw+2*gap
  for i in range(3):
   im=images[i][2].resize((pw,ph),Image.Resampling.LANCZOS)
   y=int(h*.18+(1-smooth((t-8-i*.18)/.8))*h*.75)
   canvas.paste(im,((w-total)//2+i*(pw+gap),y))
 canvas.save(frames/f'{f:04d}.png')
 if f in [12,36,60,92,124,156,188,220,252,280]:canvas.save(a.root/f'frame-{f:04d}.png')
video=a.root/f'resonance-atlas-{w}.mp4'
subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-framerate',str(fps),'-i',str(frames/'%04d.png'),'-c:v','libx264','-threads','2','-preset','medium','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(video)],check=True)
subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],check=True)
result=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)],capture_output=True,text=True,check=True)
(a.root/f'video-{w}-probe.json').write_text(result.stdout)
print(video)
