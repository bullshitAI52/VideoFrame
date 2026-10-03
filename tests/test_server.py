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
