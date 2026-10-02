#!/usr/bin/env python3
import base64, concurrent.futures, hashlib, io, json, mimetypes, os, shutil, subprocess, threading, time, uuid
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('VIDEOFRAME_DATA',str(Path.home()/'Library/Application Support/VideoFrame')))
DATA.mkdir(parents=True,exist_ok=True)
LOCK=threading.RLock(); STOP=threading.Event(); BUSY=False
STATEFILE=DATA/'state.json'; CONFIGFILE=DATA/'config.json'
def read(p,default):
    try:return json.loads(p.read_text())
    except (OSError,ValueError):return default
S=read(STATEFILE,{'directory':'','videos':[],'sheets':[],'message':'请选择视频目录','intent':''})
C=read(CONFIGFILE,{'base_url':'','key':'','model':''})
def save():
    with LOCK:
        tmp=DATA/'state.tmp'; tmp.write_text(json.dumps(S,ensure_ascii=False)); tmp.replace(STATEFILE)
def config_save():
    fd=os.open(CONFIGFILE,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(C,f)
def probe(p):
    d=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(p)],timeout=30))
    return float(d['format']['duration'])
def scan(directory):
    p=Path(directory).expanduser().resolve()
    if not p.is_dir():raise ValueError('目录不存在')
    if S['directory'] != str(p):
        if S['directory']:
            snapshot=DATA/('workspace_'+hashlib.sha256(S['directory'].encode()).hexdigest()[:20]+'.json')
            snapshot.write_text(json.dumps(S,ensure_ascii=False))
        previous=read(DATA/('workspace_'+hashlib.sha256(str(p).encode()).hexdigest()[:20]+'.json'),{'directory':str(p),'videos':[],'sheets':[],'intent':''})
        S.update(previous)
    old={v['path']:v for v in S['videos']}
    videos=[]
    for f in sorted(p.rglob('*')):
        if f.suffix.lower() in {'.mp4','.mov','.mkv','.avi','.ts','.m4v','.webm','.mts'} and f.is_file():
            stamp=f'{f}:{f.stat().st_size}:{f.stat().st_mtime_ns}'
            ident=hashlib.sha256(stamp.encode()).hexdigest()[:20]
            v=old.get(str(f))
            videos.append(v if v and v['id']==ident else {'id':ident,'path':str(f),'name':f.name,'status':'待抽帧','duration':0})
    with LOCK:
        S.update(directory=str(p),videos=videos,sheets=[s for s in S['sheets'] if s['video_id'] in {v['id'] for v in videos}],message=f'已扫描 {len(videos)} 个视频')
        save()
def extract(interval,threshold):
    for v in S['videos']:
        if STOP.is_set():break
        if v['status']=='已完成':continue
        folder=DATA/v['id']; folder.mkdir(exist_ok=True)
        try:
            v['status']='抽帧中'; v['duration']=probe(v['path']);save()
            # fps supplies a bounded sampling rate; mpdecimate removes near-identical frames.
            filt=f'fps=1/{interval},scale=480:-2'
            if threshold:filt+=',mpdecimate'
            filt+=',showinfo'
            log=folder/'extract.log'
            for f in folder.glob('frame_*.jpg'):f.unlink()
            with log.open('w') as out:
                proc=subprocess.Popen(['ffmpeg','-hide_banner','-y','-i',v['path'],'-vf',filt,'-fps_mode','vfr','-q:v','3',str(folder/'frame_%06d.jpg')],stderr=out,stdout=subprocess.DEVNULL)
                while proc.poll() is None:
                    if STOP.wait(.2):proc.terminate();proc.wait();break
            if STOP.is_set():v['status']='待抽帧';save();break
            if proc.returncode:raise RuntimeError(log.read_text()[-500:])
            import re
            stamps=[float(x) for x in re.findall(r'pts_time:([\d.]+)',log.read_text())]
            frames=sorted(folder.glob('frame_*.jpg')); sheets=[]
            for start in range(0,len(frames),16):
                batch=frames[start:start+16]; canvas=Image.new('RGB',(1280,720),'black'); times=[]
                for j,f in enumerate(batch):
                    with Image.open(f) as im:
                        im.thumbnail((320,180)); x=(j%4)*320; y=(j//4)*180
                        canvas.paste(im,(x+(320-im.width)//2,y+(180-im.height)//2))
                    t=stamps[start+j] if start+j<len(stamps) else (start+j)*interval;times.append(t)
                    ImageDraw.Draw(canvas).text((x+7,y+7),f'{j+1:02} | {int(t)//3600:02}:{int(t)%3600//60:02}:{int(t)%60:02}',fill='white',stroke_width=1,stroke_fill='black')
                sid=f"{v['id']}_{start//16+1:04}";canvas.save(folder/f'{sid}.jpg',quality=88)
                sheets.append({'id':sid,'video_id':v['id'],'name':v['name'],'count':len(batch),'times':times,'favorite':False,'analysis':None})
            with LOCK:
                S['sheets']=[s for s in S['sheets'] if s['video_id']!=v['id']]+sheets
                v['status']='已完成';S['message']=f"已处理 {v['name']} · {len(sheets)} 张宫格";save()
            for f in frames:f.unlink()
        except Exception as e:v['status']='失败';v['error']=str(e);save()
def analyze_one(s,intent):
    if STOP.is_set():return
    try:
        raw=(DATA/s['video_id']/f"{s['id']}.jpg").read_bytes()
        prompt=f'分析监控视频宫格，用户要寻找：{intent}。图像格子编号从左到右、从上到下，从1开始。只有前{s["count"]}格有效。只按可见证据判断，不推断身份或犯罪。返回严格JSON：{{"hit":true或false,"confidence":0到100,"frames":[命中格子编号],"reason":"简短中文说明"}}。'
        r=requests.post(C['base_url'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+C['key']},json={'model':C['model'],'messages':[{'role':'user','content':[{'type':'text','text':prompt},{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(raw).decode()}}]}]},timeout=(15,150))
        r.raise_for_status();txt=r.json()['choices'][0]['message']['content'].strip()
        if txt.startswith('```'):txt=txt.split('\n',1)[1].rsplit('```',1)[0]
        a=json.loads(txt)
        if type(a.get('hit')) is not bool:raise ValueError('接口返回的 hit 不是布尔值')
        a={'hit':a['hit'],'confidence':max(0,min(100,float(a.get('confidence',0)))),'reason':str(a.get('reason','')),'frames':[int(n) for n in a.get('frames',[]) if isinstance(n,int) and 1<=n<=s['count']]}
        with LOCK:s['analysis']=a; s.pop('error',None);save()
    except Exception as e:
        with LOCK:s['error']=str(e)[:500];save()
def analyze(intent,restart):
    S['intent']=intent
    if restart:
        for s in S['sheets']:s['analysis']=None;s.pop('error',None)
    save()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending=set()
        for s in S['sheets']:
            if STOP.is_set():break
            if s['analysis']:continue
            while len(pending)>=4:
                _,pending=concurrent.futures.wait(pending,return_when=concurrent.futures.FIRST_COMPLETED)
            if not STOP.is_set():pending.add(pool.submit(analyze_one,s,intent))
def launch(fn,*args):
    global BUSY
    if BUSY:raise ValueError('任务正在执行，请先暂停并等待当前任务结束')
    BUSY=True;STOP.clear()
    def run():
        global BUSY
        try:fn(*args)
        except Exception as e:S['message']=str(e)
        finally:BUSY=False;S['message']='任务已暂停' if STOP.is_set() else '任务已结束';save()
    threading.Thread(target=run,daemon=True).start()
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def respond(self,obj,status=200):
        b=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=='/api/state':
            with LOCK:self.respond(dict(S,busy=BUSY,ffmpeg=bool(shutil.which('ffmpeg') and shutil.which('ffprobe')),config={'base_url':C['base_url'],'model':C['model'],'has_key':bool(C['key'])}))
            return
        if u.path=='/media':
            sid=parse_qs(u.query).get('id',[''])[0];s=next((s for s in S['sheets'] if s['id']==sid),None)
            if not s:self.send_error(404);return
            p=DATA/s['video_id']/f'{sid}.jpg'
        else:
            p=ROOT/'static'/('index.html' if u.path=='/' else u.path.lstrip('/'))
            if not p.resolve().is_relative_to((ROOT/'static').resolve()):self.send_error(403);return
        if not p.is_file():self.send_error(404);return
        b=p.read_bytes();self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(str(p))[0] or 'application/octet-stream');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_POST(self):
        # Reject cross-origin requests and non-JSON forms to protect local actions.
        origin=self.headers.get('Origin')
        if origin and origin!=f'http://{self.headers.get("Host")}':self.respond({'error':'拒绝跨域请求'},403);return
        if 'application/json' not in self.headers.get('Content-Type',''):self.respond({'error':'需要 JSON'},415);return
        try:
            d=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or '{}');path=self.path
            if path=='/api/choose':
                if BUSY:raise ValueError('请先暂停任务')
                p=subprocess.check_output(['osascript','-e','POSIX path of (choose folder with prompt "选择视频目录")'],text=True).strip();scan(p)
            elif path=='/api/scan':
                if BUSY:raise ValueError('请先暂停任务')
                scan(d['directory'])
            elif path=='/api/extract':
                interval=float(d.get('interval',5))
                if not .5<=interval<=3600:raise ValueError('抽帧间隔应为 0.5–3600 秒')
                launch(extract,interval,bool(d.get('dedupe',True)))
            elif path=='/api/stop':STOP.set()
            elif path=='/api/favorite':
                with LOCK:
                    s=next(s for s in S['sheets'] if s['id']==d['id']);s['favorite']=not s['favorite'];save()
            elif path=='/api/config':
                if BUSY:raise ValueError('任务运行时不能更改接口')
                url=d['base_url'].strip()
                if not url.startswith(('https://','http://localhost:','http://127.0.0.1:')):raise ValueError('远程接口需使用 HTTPS')
                C.update(base_url=url,model=d['model'].strip())
                if d.get('key'):C['key']=d['key'].strip()
                config_save()
            elif path=='/api/analyze':
                if not all(C.values()):raise ValueError('请先配置接口地址、模型和密钥')
                intent=d.get('intent','').strip()
                if len(intent)<10:raise ValueError('请至少输入 10 个字的分析意图')
                if S['intent'] and intent!=S['intent'] and not d.get('restart'):raise ValueError('分析意图已更改，请点击重新分析')
                launch(analyze,intent,bool(d.get('restart')))
            elif path=='/api/reveal':
                s=next(s for s in S['sheets'] if s['id']==d['id']);v=next(v for v in S['videos'] if v['id']==s['video_id']);subprocess.run(['open','-R',v['path']],check=True)
            else:raise ValueError('未知操作')
            self.respond({'ok':True})
        except Exception as e:self.respond({'error':str(e)},400)
if __name__=='__main__':
    for v in S['videos']:
        if v['status']=='抽帧中':v['status']='待抽帧'
    port=int(os.environ.get('VIDEOFRAME_PORT','8765'))
    print(f'VideoFrame: http://127.0.0.1:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
