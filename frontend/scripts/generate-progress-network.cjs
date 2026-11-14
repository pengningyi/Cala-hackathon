/* Reproduce with: node scripts/generate-progress-network.cjs
 * Requires existing Playwright/Edge and Python with Pillow (PYTHON_BINARY can override it).
 * Native Canvas generates every frame. Pillow only quantizes/encodes the GIF.
 */
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { chromium } = require('@playwright/test');

const root = path.resolve(__dirname, '..');
const work = path.join(root, '.qa', 'progress-network');
const output = path.join(root, 'public', 'obsidian-network.gif');
const SIZE = 600, FRAMES = 64, DURATION = 90;

/** This function runs unchanged in the browser and in the saved native preview. */
function setupNetwork({ size }) {
  const canvas = document.getElementById('network');
  canvas.width = size; canvas.height = size;
  let seed = 40931;
  const random = () => { seed = Math.imul(seed, 1664525) + 1013904223 | 0; return (seed >>> 0) / 4294967296; };
  const tau = Math.PI * 2;
  const centers = [[.46,.47],[.34,.32],[.34,.59],[.52,.64],[.65,.61],[.67,.43],
    [.64,.20],[.78,.75],[.40,.82],[.18,.48],[.20,.26],[.20,.74],[.84,.36],[.85,.57],[.58,.86]];
  const nodes = [], edges = [], clusters = [], hubIndices = [];
  const addEdge = (a, b) => {
    if (a === b || edges.some(edge => edge.a === Math.min(a,b) && edge.b === Math.max(a,b))) return;
    edges.push({ a: Math.min(a,b), b: Math.max(a,b) });
  };
  centers.forEach(([x,y], index) => {
    const hub = nodes.length, spread = index < 6 ? 57 : 43 + random() * 10;
    const phase = random() * tau;
    nodes.push({ x:x*600, y:y*600, radius:index < 6 ? 4.5 + random()*1.4 : 3 + random(), hub:index, phase, isHub:true });
    hubIndices.push(hub);
    const members = [hub], count = 12 + Math.floor(random() * 8);
    for (let i = 0; i < count; i++) {
      const angle = i * tau / count + (random()-.5)*.35, distance = spread*(.31 + Math.sqrt(random())*.69);
      const node = nodes.length;
      nodes.push({ x:x*600+Math.cos(angle)*distance, y:y*600+Math.sin(angle)*distance,
        radius:1.25+random()*1.1, hub:index, phase:random()*tau, isHub:false });
      members.push(node); addEdge(hub,node);
      if (i > 0 && random() < .70) addEdge(node,node-1);
      if (i > 2 && random() < .24) addEdge(node, members[1+Math.floor(random()*(members.length-1))]);
    }
    clusters.push(members);
  });
  // Interconnected central clusters, with sparse satellites like the reference.
  centers.forEach((point,index) => {
    const nearest = centers.map((other,i) => ({ i, d:Math.hypot(point[0]-other[0],point[1]-other[1]) }))
      .filter(other => other.i !== index).sort((a,b)=>a.d-b.d);
    for (const other of nearest.slice(0,index < 6 ? 3 : 1)) {
      addEdge(hubIndices[index],hubIndices[other.i]);
      if (index < 6) addEdge(clusters[index][2],clusters[other.i][3]);
    }
  });
  for (let i=0;i<13;i++) nodes.push({ x:36+random()*528,y:35+random()*530,radius:1.3+random(),hub:-1,phase:random()*tau,isHub:false });
  const pulses = edges.filter(edge => nodes[edge.a].isHub && nodes[edge.b].isHub).slice(0,5)
    .concat([edges[9],edges[49],edges[104],edges[194]]).filter(Boolean);

  function renderFrame(phase, destination=canvas) {
    const context = destination.getContext('2d');
    const scale = destination.width/600;
    context.setTransform(scale,0,0,scale,0,0);
    context.fillStyle='#ffffff'; context.fillRect(0,0,600,600);
    const time = tau*phase;
    const drift = centers.map((_,i)=>({x:Math.sin(time+i*1.83)*5+Math.sin(time*2+i*.71)*1.4,
      y:Math.cos(time+i*1.37)*4+Math.sin(time*2+i*.93)*1.2}));
    const points = nodes.map(node=>({x:node.x+(node.hub>=0?drift[node.hub].x:0)+Math.sin(time+node.phase)*1.4,
      y:node.y+(node.hub>=0?drift[node.hub].y:0)+Math.cos(time*2+node.phase)*1.1}));
    context.lineWidth=.85;
    context.strokeStyle='rgba(145,145,151,.28)';
    context.beginPath();
    for (const edge of edges) { const a=points[edge.a],b=points[edge.b]; context.moveTo(a.x,a.y);context.lineTo(b.x,b.y); }
    context.stroke();
    // Quiet, moving lavender signals. A sine envelope fades each wrap to zero.
    pulses.forEach((edge,index)=>{
      const progress=(phase*2+index*.137)%1, visibility=Math.sin(Math.PI*progress)**2;
      const a=points[edge.a],b=points[edge.b],from=Math.max(0,progress-.24);
      const start={x:a.x+(b.x-a.x)*from,y:a.y+(b.y-a.y)*from};
      const head={x:a.x+(b.x-a.x)*progress,y:a.y+(b.y-a.y)*progress};
      const gradient=context.createLinearGradient(start.x,start.y,head.x+.001,head.y+.001);
      gradient.addColorStop(0,'rgba(157,121,189,0)'); gradient.addColorStop(1,`rgba(157,121,189,${visibility*.85})`);
      context.strokeStyle=gradient;context.lineWidth=1.6;context.beginPath();context.moveTo(start.x,start.y);context.lineTo(head.x,head.y);context.stroke();
      context.fillStyle=`rgba(173,143,199,${visibility*.15})`;context.beginPath();context.arc(head.x,head.y,6,0,tau);context.fill();
      context.fillStyle=`rgba(153,113,185,${visibility*.90})`;context.beginPath();context.arc(head.x,head.y,2.3,0,tau);context.fill();
    });
    nodes.forEach((node,index)=>{
      const point=points[index];
      context.fillStyle=node.isHub?'#78787d':`rgba(116,116,123,${.46+node.radius*.13})`;
      context.beginPath();context.arc(point.x,point.y,node.radius,0,tau);context.fill();
      if(node.isHub && node.hub%3===0){
        const breath=.5+.5*Math.sin(time+node.phase), radius=node.radius+4+breath*2;
        context.fillStyle=`rgba(170,142,194,${.06+breath*.09})`;context.beginPath();context.arc(point.x,point.y,radius,0,tau);context.fill();
        context.strokeStyle=`rgba(158,126,184,${.15+breath*.21})`;context.lineWidth=.9;
        context.beginPath();context.arc(point.x,point.y,radius,0,tau);context.stroke();
        context.fillStyle=`rgba(145,108,175,${.25+breath*.33})`;context.beginPath();context.arc(point.x,point.y,node.radius,0,tau);context.fill();
      }
    });
  }
  renderFrame(0);
  window.__network = { renderFrame, canvas, nodes:nodes.length, edges:edges.length };
}

const encode = String.raw`
import hashlib,json,sys
from pathlib import Path
from PIL import Image

spec=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
# Fixed global palette: white/neutral grays and two lavender ramps. No per-frame palettes or dithering.
palette=[]
for i in range(128):
    shade=round(255-i*183/127)
    palette.extend([shade,shade,shade])
for start,end in [((255,255,255),(155,118,187)),((214,203,224),(115,80,142))]:
    for i in range(64):
        t=i/63
        palette.extend([round(a+(b-a)*t) for a,b in zip(start,end)])
reference=Image.new('P',(1,1))
reference.putpalette(palette)
frames=[]
for file in spec['files']:
    with Image.open(file) as image:
        frames.append(image.convert('RGB').quantize(palette=reference,dither=Image.Dither.NONE))
frames[0].save(spec['output'],format='GIF',save_all=True,append_images=frames[1:],duration=spec['duration'],
    loop=0,disposal=1,optimize=False,palette=bytes(palette))
with Image.open(spec['output']) as gif:
    hashes=[]
    durations=[]
    for index in range(gif.n_frames):
        gif.seek(index)
        hashes.append(hashlib.sha256(gif.convert('RGB').tobytes()).hexdigest())
        durations.append(gif.info.get('duration',0))
    metadata={'format':gif.format,'size':list(gif.size),'frames':gif.n_frames,'duration_ms':sum(durations),
        'frame_duration_ms':sorted(set(durations)),'loop':gif.info.get('loop'),'unique_frames':len(set(hashes)),
        'bytes':Path(spec['output']).stat().st_size,'signature':Path(spec['output']).read_bytes()[:6].decode('ascii')}
    assert metadata['signature']=='GIF89a' and metadata['frames']==spec['frames'] and metadata['loop']==0
    assert metadata['unique_frames']==spec['frames']
    assert metadata['bytes']<3*1024*1024,metadata
Path(spec['report']).write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
print(json.dumps(metadata))
`;

(async()=>{
  fs.mkdirSync(work,{recursive:true});fs.mkdirSync(path.dirname(output),{recursive:true});
  const html=`<!doctype html><html><meta charset="utf-8"><title>Decorative network animation preview</title><body style="margin:0;background:white"><canvas id="network"></canvas><script>(${setupNetwork.toString()})({size:${SIZE}});</script></body></html>`;
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const files=[];
  try{
    const page=await browser.newPage({viewport:{width:SIZE,height:SIZE},deviceScaleFactor:1});
    await page.setContent(html);
    for(let frame=0;frame<FRAMES;frame++){
      const data=await page.evaluate(phase=>{window.__network.renderFrame(phase);return window.__network.canvas.toDataURL('image/png');},frame/FRAMES);
      const file=path.join(work,`frame-${String(frame).padStart(3,'0')}.png`);
      fs.writeFileSync(file,Buffer.from(data.split(',')[1],'base64'));files.push(file);
    }
    const contact=await page.evaluate(()=>{
      const sheet=document.createElement('canvas');sheet.width=900;sheet.height=600;
      const context=sheet.getContext('2d');context.fillStyle='white';context.fillRect(0,0,900,600);
      for(let i=0;i<6;i++){
        const sample=document.createElement('canvas');sample.width=300;sample.height=300;
        window.__network.renderFrame(i/6,sample);context.drawImage(sample,(i%3)*300,Math.floor(i/3)*300);
      }
      return sheet.toDataURL('image/png');
    });
    fs.writeFileSync(path.join(work,'contact-sheet.png'),Buffer.from(contact.split(',')[1],'base64'));
    const preview=html.replace('</body>',`<script>const epoch=performance.now();function animate(now){window.__network.renderFrame(((now-epoch)%${FRAMES*DURATION})/${FRAMES*DURATION});requestAnimationFrame(animate);}requestAnimationFrame(animate);</script></body>`);
    fs.writeFileSync(path.join(work,'preview.html'),preview);
  }finally{await browser.close();}
  const spec=path.join(work,'encoding.json');
  fs.writeFileSync(spec,JSON.stringify({files,frames:FRAMES,duration:DURATION,output,report:path.join(work,'metadata.json')}));
  const result=spawnSync(process.env.PYTHON_BINARY||'python',['-c',encode,spec],{encoding:'utf8',windowsHide:true});
  if(result.status!==0)throw new Error(result.stderr||result.stdout||'GIF encoding failed');
  process.stdout.write(result.stdout);
})().catch(error=>{process.stderr.write(`${error.message}\n`);process.exitCode=1;});
