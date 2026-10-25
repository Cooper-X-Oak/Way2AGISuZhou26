"""Temporary public demo: one HTML, anonymous private sessions, isolated runs.

uv run --env-file .env uvicorn server.public:app --host 127.0.0.1 --port 8788
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse

from server.app import create_app
from server.runs import RunManager

COOKIE='way2agi_demo_session'
ROOT=Path(__file__).resolve().parents[1]


class SessionAccess:
    def __init__(self,root):
        self.path=Path(root)/'session-owners.json'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.owners=json.loads(self.path.read_text(encoding='utf8')) if self.path.exists() else {}
        self.posts=deque()

    def owns(self,request,run_id):
        with self.lock:return self.owners.get(run_id)==request.state.public_session

    def check(self,request,run_id):
        if not self.owns(request,run_id):raise HTTPException(404,'任务不存在')

    def record(self,request,run_id):
        with self.lock:
            self.owners[run_id]=request.state.public_session
            temporary=self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.owners),encoding='utf8')
            temporary.replace(self.path)

    def permit(self,session):
        with self.lock:
            now=time.monotonic()
            while self.posts and self.posts[0][0]<=now-60:self.posts.popleft()
            if len(self.posts)>=30 or sum(owner==session for _,owner in self.posts)>=10:return False
            self.posts.append((now,session));return True


def create_public_app(root=None,manager=None):
    root=Path(root or os.getenv('PUBLIC_DEMO_ROOT',str(ROOT/'.work/tmp/public-demo-runtime')))
    access=SessionAccess(root)
    manager=manager or RunManager(root,source_root=ROOT)
    app=create_app(manager,task_access=access)
    app.state.public_access=access

    @app.middleware('http')
    async def public_boundary(request,call_next):
        path=request.url.path
        allowed=path in ('/','/agent-workbench.html','/api/health','/api/profiles','/api/areas','/api/tasks','/api/tasks/config') or re.fullmatch(r'/api/tasks/[a-f0-9]{32}(?:/view)?',path)
        if not allowed:return JSONResponse({'detail':'Not found'},status_code=404)
        if request.method not in ('GET','HEAD') and not (request.method=='POST' and path=='/api/tasks'):
            return JSONResponse({'detail':'Method not allowed'},status_code=405)
        origin=request.headers.get('origin')
        if request.method=='POST' and origin and urlsplit(origin).netloc!=request.headers.get('host'):
            return JSONResponse({'detail':'跨站请求未执行'},status_code=403)
        token=request.cookies.get(COOKIE,'')
        fresh=not re.fullmatch(r'[a-f0-9]{64}',token)
        if fresh:token=secrets.token_hex(32)
        request.state.public_session=hashlib.sha256(token.encode()).hexdigest()
        if request.method=='POST' and not access.permit(request.state.public_session):
            response=JSONResponse({'detail':'请求过于频繁，请稍后再试'},status_code=429,headers={'Retry-After':'60'})
        else:response=await call_next(request)
        if fresh:response.set_cookie(COOKIE,token,max_age=7*24*3600,httponly=True,secure=True,samesite='lax')
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Robots-Tag']='noindex, nofollow'
        return response

    @app.get('/')
    @app.get('/agent-workbench.html')
    def workbench():
        return FileResponse(ROOT/'web/data/agent-workbench.html',media_type='text/html')

    return app


app=create_public_app()
