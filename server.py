#!/usr/bin/env python3
import base64, concurrent.futures, hashlib, math, json, mimetypes, os, shutil, subprocess, threading
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
    previous=S
    if S['directory'] != str(p):
        previous=read(DATA/('workspace_'+hashlib.sha256(str(p).encode()).hexdigest()[:20]+'.json'),{'directory':str(p),'videos':[],'sheets':[],'intent':''})
    old={v['path']:v for v in previous['videos']}
    videos=[]
    for f in sorted(p.rglob('*')):
        if f.suffix.lower() in {'.mp4','.mov','.mkv','.avi','.ts','.m4v','.webm','.mts'} and f.is_file():
            stamp=f'{f}:{f.stat().st_size}:{f.stat().st_mtime_ns}'
            ident=hashlib.sha256(stamp.encode()).hexdigest()[:20]
            v=old.get(str(f))
            videos.append(v if v and v['id']==ident else {'id':ident,'path':str(f),'name':f.name,'status':'待抽帧','duration':0})
    with LOCK:
        if S['directory'] and S['directory'] != str(p):
            snapshot=DATA/('workspace_'+hashlib.sha256(S['directory'].encode()).hexdigest()[:20]+'.json')
            snapshot.write_text(json.dumps(S,ensure_ascii=False))
        S.update(directory=str(p),videos=videos,sheets=[s for s in previous['sheets'] if s['video_id'] in {v['id'] for v in videos}],intent=previous.get('intent',''),message=f'已扫描 {len(videos)} 个视频')
        save()
SEGMENT_SECONDS=3600

def extract_segment(v,folder,segment,start_time,length,interval,threshold):
    """Produce one checkpoint's sheets; uncommitted files are safe to overwrite."""
    import re
    scratch=folder/'pending'
    scratch.mkdir(exist_ok=True)
    for f in scratch.glob('frame_*.jpg'):f.unlink()
    filt=f'fps=1/{interval},scale=480:-2'
    if threshold:filt+=',mpdecimate'
    filt+=',showinfo'
    log=scratch/'extract.log'
    with log.open('w') as out:
        proc=subprocess.Popen(['ffmpeg','-hide_banner','-y','-ss',str(start_time),'-i',v['path'],'-t',str(length),'-vf',filt,'-fps_mode','vfr','-q:v','3',str(scratch/'frame_%06d.jpg')],stderr=out,stdout=subprocess.DEVNULL)
        while proc.poll() is None:
            if STOP.wait(.2):
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
                break
    if STOP.is_set():raise InterruptedError()
    if proc.returncode and length>=interval:raise RuntimeError(log.read_text()[-500:])
    stamps=[float(x) for x in re.findall(r'pts_time:([-+0-9.eE]+)',log.read_text())]
    frames=sorted(scratch.glob('frame_*.jpg'))
    if not frames:
        first=scratch/'frame_000001.jpg'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-ss',str(start_time),'-i',v['path'],'-t',str(length),'-frames:v','1','-vf','scale=480:-2',str(first)],check=True,capture_output=True,timeout=60)
        if not first.is_file():raise ValueError('视频未能解码出任何画面')
        frames=[first];stamps=[0.0]
    sheets=[]
    for start in range(0,len(frames),16):
        if STOP.is_set():raise InterruptedError()
        batch=frames[start:start+16];canvas=Image.new('RGB',(1280,720),'black');times=[]
        for j,f in enumerate(batch):
            with Image.open(f) as im:
                im.thumbnail((320,180));x=(j%4)*320;y=(j//4)*180
                canvas.paste(im,(x+(320-im.width)//2,y+(180-im.height)//2))
            relative=stamps[start+j] if start+j<len(stamps) else (start+j)*interval
            t=start_time+relative;times.append(t)
            ImageDraw.Draw(canvas).text((x+7,y+7),f'{j+1:02} | {int(t)//3600:02}:{int(t)%3600//60:02}:{int(t)%60:02}',fill='white',stroke_width=1,stroke_fill='black')
        sid=f"{v['id']}_h{segment+1:04}_{start//16+1:04}"
        target=folder/f'{sid}.jpg';temporary=folder/f'{sid}.tmp'
        canvas.save(temporary,format='JPEG',quality=88);temporary.replace(target)
        sheets.append({'id':sid,'video_id':v['id'],'name':v['name'],'count':len(batch),'times':times,'segment':segment,'segment_start':start_time,'favorite':False,'analysis':None})
    if STOP.is_set():raise InterruptedError()
    return sheets

def extract(interval,threshold):
    for v in S['videos']:
        if STOP.is_set():break
        if v['status']=='已完成':continue
        folder=DATA/v['id'];folder.mkdir(exist_ok=True)
        try:
            with LOCK:
                v['status']='抽帧中';v.pop('error',None);save()
            duration=probe(v['path'])
            if not math.isfinite(duration) or duration<=0:raise ValueError('无法读取有效视频时长')
            with LOCK:
                v['duration']=duration
                # Pin settings so resumed videos never mix different sampling rules.
                settings=v.setdefault('extract_settings',{'interval':interval,'dedupe':threshold,'segment_seconds':SEGMENT_SECONDS})
                segment_seconds=settings['segment_seconds']
                total=math.ceil(duration/segment_seconds)
                v['segments_total']=total
                completed=v.setdefault('completed_segments',[])
                save()
            for segment in range(total):
                if STOP.is_set():raise InterruptedError()
                if segment in completed:continue
                start_time=segment*segment_seconds
                with LOCK:
                    v['current_segment']=segment
                    S['message']=f"{v['name']} · 正在处理第 {segment+1}/{total} 段（每段最多 1 小时）";save()
                sheets=extract_segment(v,folder,segment,start_time,min(segment_seconds,duration-start_time),settings['interval'],settings['dedupe'])
                with LOCK:
                    if STOP.is_set():raise InterruptedError()
                    old_sheets=S['sheets']
                    S['sheets']=[s for s in old_sheets if not (s['video_id']==v['id'] and s.get('segment')==segment)]+sheets
                    completed.append(segment)
                    try:save()
                    except Exception:
                        completed.remove(segment);S['sheets']=old_sheets;raise
                # Release temporary disk space after each committed hour.
                for f in (folder/'pending').glob('frame_*.jpg'):f.unlink()
            with LOCK:
                v['status']='已完成';v.pop('current_segment',None);save()
        except InterruptedError:
            with LOCK:v['status']='待抽帧';v.pop('current_segment',None);save()
            break
        except Exception as e:
            with LOCK:v['status']='失败';v['error']=str(e);save()
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
        confidence=float(a.get('confidence',0))
        if not math.isfinite(confidence):raise ValueError('接口返回的置信度不是有限数字')
        if not isinstance(a.get('frames',[]),list):raise ValueError('接口返回的 frames 不是数组')
        a={'hit':a['hit'],'confidence':max(0,min(100,confidence)),'reason':str(a.get('reason','')),'frames':[int(n) for n in a.get('frames',[]) if type(n) is int and 1<=n<=s['count']]}
        with LOCK:s['analysis']=a; s.pop('error',None);save()
    except Exception as e:
        with LOCK:s['error']=str(e)[:500];save()
def analyze(intent,restart):
    with LOCK:
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
    with LOCK:
        if BUSY:raise ValueError('任务正在执行，请先暂停并等待当前任务结束')
        BUSY=True;STOP.clear()
    def run():
        global BUSY
        error=None
        try:fn(*args)
        except Exception as e:error=str(e)
        finally:
            with LOCK:
                BUSY=False
                S['message']=('任务失败：'+error) if error else ('任务已暂停' if STOP.is_set() else '任务已结束（请检查各项状态）')
                save()
    thread=threading.Thread(target=run,daemon=True)
    try:thread.start()
    except Exception:
        with LOCK:BUSY=False
        raise
    return thread
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def respond(self,obj,status=200):
        b=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def stream_video(self, video_id):
        with LOCK:
            video=next((dict(v) for v in S['videos'] if v['id']==video_id),None)
        if not video:self.send_error(404,'Video not found');return
        try:stream=Path(video['path']).open('rb')
        except OSError:self.send_error(404,'Original video unavailable');return
        with stream:
            size=os.fstat(stream.fileno()).st_size
            start,end=0,size-1
            value=self.headers.get('Range')
            if value:
                import re
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',value.strip())
                try:
                    if not match or not any(match.groups()):raise ValueError()
                    left,right=match.groups()
                    if left:
                        start=int(left);end=min(int(right),size-1) if right else size-1
                    else:
                        length=int(right)
                        if length<=0:raise ValueError()
                        start=max(0,size-length)
                    if start>=size or start>end:raise ValueError()
                except ValueError:
                    self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return
            self.send_response(206 if value else 200)
            self.send_header('Content-Type',mimetypes.guess_type(video['path'])[0] or 'application/octet-stream')
            self.send_header('Accept-Ranges','bytes')
            self.send_header('Content-Length',str(max(0,end-start+1)))
            self.send_header('Cache-Control','no-store')
            if value:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            stream.seek(start);remaining=end-start+1
            try:
                while remaining>0:
                    chunk=stream.read(min(1024*1024,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)
            except (BrokenPipeError,ConnectionResetError):pass
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=='/video':
            self.stream_video(parse_qs(u.query).get('id',[''])[0]);return
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
            with LOCK:
                if path=='/api/choose':
                    if BUSY:raise ValueError('请先暂停任务')
                    p=subprocess.check_output(['osascript','-e','POSIX path of (choose folder with prompt "选择视频目录")'],text=True).strip();scan(p)
                elif path=='/api/scan':
                    if BUSY:raise ValueError('请先暂停任务')
                    scan(d['directory'])
                elif path=='/api/extract':
                    interval=float(d.get('interval',5))
                    if not .5<=interval<=3600:raise ValueError('抽帧间隔应为 0.5–3600 秒')
                    if not S['videos']:raise ValueError('请先扫描视频目录')
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
                    if not S['sheets']:raise ValueError('请先抽帧生成宫格')
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
