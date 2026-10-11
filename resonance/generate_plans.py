"""Generate bounded typed plans, never SVG. Inkscape is the vector producer."""
import json
import math
from pathlib import Path

OUT = Path(__file__).parent / 'plans'
OUT.mkdir(exist_ok=True)
W, H = 960, 1280


def make(index, title, subtitle, bg, fg, accent, secondary):
    nodes = []
    def n(kind, name, **kw):
        nodes.append(dict(type=kind, id=name, **kw))
    def text(name, value, x, y, size=18, **kw):
        n('text', name, text=value, x=x, y=y, font_family='DejaVu Sans', font_size=size, fill=fg, **kw)
    def path(name, points, stroke=accent, width=2, **kw):
        d = ' '.join(('M' if i == 0 else 'L') + f'{x:.3f},{y:.3f}' for i,(x,y) in enumerate(points))
        n('path',name,d=d,fill='none',stroke=stroke,stroke_width=width,**kw)
    n('layer','paper',label='01 / Paper')
    n('rect','background',parent='paper',x=0,y=0,width=W,height=H,fill=bg)
    n('layer','grid',label='02 / Measurement grid')
    for x in range(80,881,40):
        path(f'grid-x-{x}',[(x,300),(x,980)],stroke=fg,width=.7,parent='grid',opacity=.12)
    for y in range(300,981,40):
        path(f'grid-y-{y}',[(80,y),(880,y)],stroke=fg,width=.7,parent='grid',opacity=.12)
    n('layer','signal',label='03 / Signal geometry')
    if index == 1:
        # A family of 2:3 Lissajous phase portraits; no empirical data claim.
        for j in range(31):
            phase=(j-15)*.025
            points=[]
            for k in range(721):
                t=k*math.tau/720
                points.append((480+330*math.sin(2*t+phase),640+275*math.sin(3*t)))
            path(f'phase-{j}',points,stroke=accent if j%3 else secondary,width=1.35,parent='signal',opacity=.72)
    elif index == 2:
        # Equal point-source wavefronts, additive schematic, not pressure simulation.
        for j in range(1,25):
            for side,cx in [('a',365),('b',595)]:
                r=j*12
                n('circle',f'wave-{side}-{j}',parent='signal',cx=cx,cy=640,r=r,fill='none',stroke=accent if side=='a' else secondary,stroke_width=1.9,opacity=.78)
        for side,cx in [('a',365),('b',595)]:
            n('circle',f'source-{side}',parent='signal',cx=cx,cy=640,r=7,fill=fg)
    else:
        # Decaying harmonics stacked as an isometric score.
        for j in range(42):
            points=[]
            for k in range(321):
                t=k/320
                envelope=math.sin(math.pi*t)**1.5
                signal=(math.sin(t*math.tau*5+j*.085)+.35*math.sin(t*math.tau*11-j*.13))*envelope
                points.append((105+650*t+j*2.25,405+j*10.2-48*signal))
            path(f'harmonic-{j}',points,stroke=accent if j<24 else secondary,width=2,parent='signal',opacity=.9)
    n('layer','annotations',label='04 / Notes and scales')
    path('rule',[(64,1050),(896,1050)],stroke=fg,width=1,parent='annotations')
    text('note',subtitle,64,1093,19,parent='annotations')
    details={1:['x = sin(2t + phase)','y = sin(3t)','31 PHASE OFFSETS'],2:['Two equal point sources','Concentric wavefront study','48 WAVEFRONTS'],3:['Five cycles + eleventh harmonic','A decaying envelope in depth','42 SIGNAL TRACES']}[index]
    text('formula-a',details[0],64,1135,16,parent='annotations',opacity=.72)
    text('formula-b',details[1],64,1161,16,parent='annotations',opacity=.72)
    text('count',details[2],896,1135,13,parent='annotations',text_anchor='end')
    for x in [80,280,480,680,880]:
        path(f'tick-{x}',[(x,987),(x,997)],stroke=fg,width=1,parent='annotations')
    text('scale-a','0',80,1022,12,parent='annotations')
    text('scale-b','NORMALIZED / SCHEMATIC',480,1022,12,parent='annotations',text_anchor='middle',letter_spacing=2)
    text('scale-c','1',880,1022,12,parent='annotations',text_anchor='end')
    n('layer','type',label='05 / Editorial typography')
    text('brand','RESONANCE ATLAS',64,66,15,parent='type',letter_spacing=4)
    text('edition','VECTOR STUDIES / 2026',896,66,13,parent='type',text_anchor='end',letter_spacing=1)
    text('number',f'0{index}',64,166,54,parent='type',font_weight='bold',opacity=.4)
    text('title',title,64,252,70,parent='type',font_weight='bold',letter_spacing=-3)
    text('footer','FORM FOLLOWS FREQUENCY',64,1234,12,parent='type',letter_spacing=2)
    text('footer-index',f'RESEARCH SERIES    0{index} / 03',896,1234,12,parent='type',text_anchor='end')
    return nodes

palette=[('Phase / locked','A visual study of rational frequency ratios.','#101c2b','#f0eadc','#d2ff60','#5ebdb0'),('Between / waves','Two origins. A shared field of interference.','#f0eadc','#132332','#c95137','#317e81'),('After / sound','A harmonic structure, suspended in time.','#32223f','#f7eddb','#f9ab72','#baa4f6')]
for i, args in enumerate(palette,1):
    nodes=make(i,*args)
    for stage,layers in [('01',{'paper','grid'}),('02',{'paper','grid','signal'}),('03',{'paper','grid','signal','annotations','type'})]:
        selected=[n for n in nodes if n['id'] in layers or n.get('parent') in layers]
        label=f'resonance-{i}-{stage}'
        (OUT/(label+'.json')).write_text(json.dumps({'output_file':'artwork/'+label+'.svg','plan':{'width':W,'height':H,'nodes':selected}},indent=2))
print('Created 9 typed plans; no SVG written.')
