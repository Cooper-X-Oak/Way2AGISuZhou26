"""Local JSON API. Run a single uvicorn worker to enforce the two-run limit."""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from pipeline.engine import NAMES
from pipeline.io import read
from pipeline.profiles import PROFILE_DIR, load
from server.events import stream_events
from server.runs import RunManager

ARTIFACTS = {f'{name}.json' for name in (*NAMES, *NAMES.values())} | {
    'inputs.json', 'params.json', 'pois.json', 'coverage.json', 'profile.json',
    'market.json', 'trace.json', 'status.json',
} | {f'{stage}.check.json' for stage in NAMES}


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    area: str = Field(min_length=1, max_length=80)
    profile: str = Field(min_length=1, max_length=80)
    input: str = Field(min_length=1, max_length=10000)
    demo_replay: Literal['compliance', 'backtrack'] | None = None
    auto_approve: bool = False


class GateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['approve', 'reject']
    note: str = Field(default='', max_length=4000)


def create_app(manager=None, task_access=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.manager = manager or RunManager()
        yield
        app.state.manager.close()

    app = FastAPI(title='市场方案流水线', lifespan=lifespan)
    app.add_middleware(CORSMiddleware,
                       allow_origin_regex=r'https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?',
                       allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'Last-Event-ID'])

    def find(request, run_id):
        mgr = request.app.state.manager
        try:
            mgr.directory(run_id)
        except KeyError:
            raise HTTPException(404, '运行不存在') from None
        return mgr

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'mode': 'rules', 'max_concurrent_runs': 2}

    @app.get('/api/areas')
    def areas():
        result = []
        for path in sorted(Path('data/areas').glob('*/coverage.json')):
            data = read(path)
            result.append({'id': path.parent.name, **{key: data.get(key) for key in
                           ('site', 'total', 'channel_counts', 'truncated', 'type_coverage')}})
        return result

    @app.get('/api/profiles')
    def profiles():
        return [dict(id=path.stem, name=load(path.stem)['product']['name'])
                for path in sorted(PROFILE_DIR.glob('*.yaml'))]

    @app.post('/api/runs', status_code=202)
    def start(body: RunRequest, request: Request):
        try:
            return {'run_id': request.app.state.manager.start(body)}
        except ValueError:
            raise HTTPException(422, '无效商圈或产品档案') from None
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from None

    @app.get('/api/runs/{run_id}')
    def status(run_id: str, request: Request):
        return find(request, run_id).status(run_id)

    @app.get('/api/runs/{run_id}/events')
    async def events(run_id: str, request: Request, last_event_id: str = Header(default='0')):
        mgr = find(request, run_id)
        if not last_event_id.isascii() or not last_event_id.isdecimal() or len(last_event_id) > 18:
            raise HTTPException(400, 'Last-Event-ID 必须是非负整数')
        return EventSourceResponse(stream_events(mgr, run_id, int(last_event_id), request))

    @app.get('/api/runs/{run_id}/artifacts/{name:path}')
    def artifact(run_id: str, name: str, request: Request):
        mgr = find(request, run_id)
        path = mgr.directory(run_id) / name
        if name in ('L2.pending.json', 'L3.pending.json'):
            with mgr.lock:
                pending = mgr.runs[run_id]['gate']
                if not pending or name != f"{pending['stage']}.pending.json":
                    raise HTTPException(404, '该关卡没有待确认预览')
                path = pending['preview']
        elif name not in ARTIFACTS:
            raise HTTPException(404, '不在交付物白名单内')
        try:
            return read(path)
        except FileNotFoundError:
            raise HTTPException(404, '交付物尚未生成') from None
        except ValueError:
            raise HTTPException(409, '交付物正在写入，请重试') from None

    @app.post('/api/runs/{run_id}/gates/{stage}')
    def gate(run_id: str, stage: str, body: GateRequest, request: Request):
        mgr = find(request, run_id)
        try:
            mgr.decide(run_id, stage, body.decision, body.note)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {'stage': stage, 'decision': body.decision, 'approved_by': 'human'}

    from server.tasks import install
    install(app, access=task_access)
    return app


app = create_app()
