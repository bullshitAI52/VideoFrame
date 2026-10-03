import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

# Isolate all tests from the user's videos, credentials and saved state.
TEMP = tempfile.TemporaryDirectory()
os.environ['VIDEOFRAME_DATA'] = TEMP.name
spec = importlib.util.spec_from_file_location('videoframe', Path(__file__).parents[1] / 'server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
EMPTY = {'directory': '', 'videos': [], 'sheets': [], 'message': '', 'intent': ''}

class ServerTests(unittest.TestCase):
    def setUp(self):
        app.S.clear(); app.S.update(copy.deepcopy(EMPTY))
        app.C.update(base_url='', key='', model='')
        app.BUSY = False; app.STOP.clear()

    def test_task_exception_remains_visible(self):
        def fail(): raise RuntimeError('specific test failure')
        app.launch(fail).join(3)
        self.assertFalse(app.BUSY)
        self.assertIn('specific test failure', app.S['message'])
        self.assertIn('失败', app.S['message'])

    def test_only_one_task_starts(self):
        release = threading.Event()
        barrier = threading.Barrier(8)
        results = []
        def start():
            barrier.wait()
            try: results.append(app.launch(lambda: release.wait(5)))
            except ValueError: results.append(None)
        threads = [threading.Thread(target=start) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join(3)
        self.assertEqual(sum(t is not None for t in results), 1)
        release.set()
        for t in results:
            if t: t.join(3)

    def test_scan_failure_preserves_workspace(self):
        app.S['directory'] = '/previous'
        before = copy.deepcopy(app.S)
        with patch.object(Path, 'rglob', side_effect=PermissionError('test denied')):
            with self.assertRaises(PermissionError): app.scan(TEMP.name)
        self.assertEqual(app.S, before)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_short_clip_has_a_frame_and_retry_clears_error(self):
        with tempfile.TemporaryDirectory(dir=TEMP.name) as folder:
            video = Path(folder) / 'short.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=size=64x64:rate=10','-t','0.2',str(video)],check=True)
            app.scan(folder)
            app.S['videos'][0]['error'] = 'old failure'
            app.extract(5, True)
            self.assertEqual(app.S['videos'][0]['status'], '已完成')
            self.assertNotIn('error', app.S['videos'][0])
            self.assertEqual(len(app.S['sheets']), 1)
            self.assertEqual(app.S['sheets'][0]['count'], 1)
            self.assertEqual(app.S['sheets'][0]['times'], [0.0])
            sheet = app.S['sheets'][0]
            self.assertTrue((app.DATA / sheet['video_id'] / (sheet['id']+'.jpg')).exists())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_normal_clip_grid_pause_resume_and_restore(self):
        with tempfile.TemporaryDirectory(dir=TEMP.name) as folder:
            video = Path(folder) / 'normal.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=64x64:rate=10','-t','18',str(video)],check=True)
            app.scan(folder)
            app.STOP.set();app.extract(1,False)
            self.assertEqual(app.S['videos'][0]['status'],'待抽帧')
            app.STOP.clear();app.extract(1,False)
            self.assertEqual([s['count'] for s in app.S['sheets']],[16,2])
            self.assertEqual(app.S['sheets'][1]['times'],[16.0,17.0])
            app.S['sheets'][0]['favorite']=True
            with tempfile.TemporaryDirectory(dir=TEMP.name) as empty:
                app.scan(empty)
                self.assertEqual(app.S['sheets'],[])
                app.scan(folder)
                self.assertTrue(app.S['sheets'][0]['favorite'])
                self.assertEqual(len(app.S['sheets']),2)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_hour_checkpoint_survives_reload_and_preserves_absolute_times(self):
        import hashlib
        with tempfile.TemporaryDirectory(dir=TEMP.name) as folder:
            video=Path(folder)/'hour.mp4'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=64x64:rate=1','-t','3722','-c:v','libx264','-preset','ultrafast',str(video)],check=True)
            original=hashlib.sha256(video.read_bytes()).hexdigest()
            app.scan(folder)
            original_save=app.save
            def pause_after_checkpoint():
                original_save()
                if app.S['videos'][0].get('completed_segments')==[0]:app.STOP.set()
            with patch.object(app,'save',side_effect=pause_after_checkpoint):app.extract(60,False)
            self.assertEqual(app.S['videos'][0]['status'],'待抽帧')
            self.assertEqual(app.S['videos'][0]['completed_segments'],[0])
            first=app.S['sheets'][0]
            first['favorite']=True;first['analysis']={'hit':True,'reason':'keep'};app.save()
            first_image=app.DATA/first['video_id']/(first['id']+'.jpg')
            first_mtime=first_image.stat().st_mtime_ns
            # Simulate a process restart by loading only on-disk state.
            app.S=json.loads(app.STATEFILE.read_text());app.STOP.clear()
            with patch.object(app,'extract_segment',wraps=app.extract_segment) as segment:
                app.extract(1,True)
                self.assertEqual(segment.call_count,1)
                self.assertEqual(segment.call_args.args[3],3600)
                self.assertEqual(segment.call_args.args[5:],(60,False))
            self.assertEqual(app.S['videos'][0]['completed_segments'],[0,1])
            self.assertEqual(app.S['videos'][0]['status'],'已完成')
            tail=[s for s in app.S['sheets'] if s['segment']==1]
            self.assertEqual(tail[0]['times'],[3600.0,3660.0])
            kept=next(s for s in app.S['sheets'] if s['id']==first['id'])
            self.assertTrue(kept['favorite']);self.assertTrue(kept['analysis']['hit'])
            self.assertEqual(first_image.stat().st_mtime_ns,first_mtime)
            self.assertEqual(hashlib.sha256(video.read_bytes()).hexdigest(),original)

    def test_interrupted_segment_is_not_committed(self):
        app.S['videos']=[{'id':'interrupted','path':'unused','name':'test','status':'待抽帧'}]
        with patch.object(app,'probe',return_value=7200),patch.object(app,'extract_segment',side_effect=InterruptedError):
            app.extract(5,True)
        self.assertEqual(app.S['videos'][0]['completed_segments'],[])
        self.assertEqual(app.S['sheets'],[])
        self.assertEqual(app.S['videos'][0]['status'],'待抽帧')

    def test_video_stream_ranges_and_unknown_ids(self):
        path=app.DATA/'range-test.mp4';path.write_bytes(bytes(range(256))*32)
        app.S['videos']=[{'id':'known','path':str(path)}]
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}/video?id=known'
        try:
            full=app.requests.get(url)
            self.assertEqual(full.status_code,200)
            self.assertEqual(full.content,path.read_bytes())
            for header,start,end in [('bytes=10-19',10,19),('bytes=8000-',8000,8191),('bytes=-10',8182,8191),('bytes=8190-9999',8190,8191)]:
                r=app.requests.get(url,headers={'Range':header})
                self.assertEqual(r.status_code,206)
                self.assertEqual(r.content,path.read_bytes()[start:end+1])
                self.assertEqual(r.headers['Content-Range'],f'bytes {start}-{end}/8192')
            for header in ['bytes=9000-','bytes=20-10','bytes=-0','bytes=0-1,3-4','garbage']:
                self.assertEqual(app.requests.get(url,headers={'Range':header}).status_code,416)
            self.assertEqual(app.requests.get(url.replace('known','missing')).status_code,404)
            path.unlink()
            self.assertEqual(app.requests.get(url).status_code,404)
        finally:server.shutdown();server.server_close();thread.join()

    def test_invalid_ai_confidence_is_failed(self):
        folder=app.DATA/'ai';folder.mkdir(exist_ok=True)
        (folder/'sheet.jpg').write_bytes(b'test-image')
        sheet={'id':'sheet','video_id':'ai','count':1,'analysis':None}
        app.S['sheets']=[sheet]
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'choices':[{'message':{'content':'{"hit":true,"confidence":NaN,"frames":[1]}'}}]}
        with patch.object(app.requests,'post',return_value=Response()): app.analyze_one(sheet,'test')
        self.assertIsNone(sheet['analysis'])
        self.assertIn('有限数字',sheet['error'])

    def test_http_blocks_mutations_during_task_and_empty_tasks(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            for path in ['extract','analyze']:
                r=app.requests.post(url+'/api/'+path,json={})
                self.assertEqual(r.status_code,400)
                self.assertIn('请先',r.json()['error'])
            app.BUSY=True
            r=app.requests.post(url+'/api/scan',json={'directory':TEMP.name})
            self.assertEqual(r.status_code,400)
            r=app.requests.post(url+'/api/config',json={'base_url':'https://example.test/v1','key':'test','model':'test'})
            self.assertEqual(r.status_code,400)
            self.assertEqual(app.C['key'],'')
        finally:
            app.BUSY=False;server.shutdown();server.server_close();thread.join()

if __name__ == '__main__': unittest.main()
