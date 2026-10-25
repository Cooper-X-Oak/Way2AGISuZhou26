"""Task input and durable, readable execution history for the Agent workspace."""
import json
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pipeline.export import public
from pipeline.io import read


from server.library import LibrarySelection


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    area: str = Field(min_length=1, max_length=80)
    profile: str = Field(min_length=1, max_length=80)
    input: str = Field(min_length=1, max_length=10000)
    mode: Literal['live', 'replay'] = 'live'
    parent_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    auto_approve: bool = True
    demo_replay: None = None
    library: LibrarySelection | None = None
    document: dict[str, str] | None = None


def lines(path):
    if not path.exists(): return []
    rows=[]
    for line in path.read_text(encoding='utf-8').splitlines():
        try: rows.append(json.loads(line))
        except ValueError: continue  # concurrent final partial line; next poll retries
    return rows


def activity(manager, run_id):
    directory=manager.directory(run_id)
    state=manager.status(run_id)
    events=[]
    for i,row in enumerate(lines(directory/'ledger.jsonl')):
        if row.get('event') not in ('stage_start','stage_done','backtrack','run_failed','run_done') and row.get('status')!='failed': continue
        events.append(dict(id=f'ledger-{i}',timestamp=row['timestamp'],kind=row.get('event','check_failed'),
                           stage=row.get('stage'),status=row.get('status'),violations=row.get('violations',[]),
                           backtrack=row.get('backtrack')))
    for i,row in enumerate(lines(directory/'agent/steps.jsonl')):
        # Display actions and evidence, not raw internal reasoning notes.
        events.append({k:v for k,v in {**row,'id':f'agent-{i}'}.items() if k!='note'})
    events.sort(key=lambda row: row['timestamp'])
    artifacts={}
    for name in ('brief','channels','discovery','zones','competition','terminals','partners','materials','pack'):
        try:
            artifact=read(directory/f'{name}.json')
            artifacts[name]={k:artifact[k] for k in ('decision','items','agent','days','backlog','request','radius_m') if k in artifact}
        except (FileNotFoundError, ValueError): pass
    try:
        pois=read(directory/'pois.json')
        names={p['id']:p for p in pois}
        for item in artifacts.get('terminals',{}).get('items',[]):
            p=names.get(item['poi_id'],{})
            item.update({k:p.get(k) for k in ('name','lat','lon','distance_m')})
    except (FileNotFoundError, ValueError): pass
    questions=[]
    try:
        brief=read(directory/'L0.json')
        labels={'delivery_people':'这次计划投入几位地推人员？','weeks':'这次计划开展几周？','radius_m':'希望覆盖商圈周边多少米？'}
        questions=[next((label for key,label in labels.items() if key in question),question)
                   for question in brief.get('questions',[])]
    except (FileNotFoundError,ValueError): pass
    return public(dict(state=state,events=events,artifacts=artifacts,questions=questions))


def install(app, access=None):
    @app.get('/api/tasks/config')
    def config():
        from pipeline.demo import CASE, RECORDING, live_ready
        missing=live_ready()
        return dict(live_ready=not missing,missing=missing,replay_available=RECORDING.is_file(),case=CASE,public_demo=access is not None)

    @app.get('/api/tasks')
    def history(request: Request):
        mgr=request.app.state.manager
        with mgr.lock:
            return sorted([mgr.status(k) for k,v in mgr.runs.items() if v.get('task') and (access is None or access.owns(request,k))],
                          key=lambda row:row['task']['created_at'],reverse=True)

    @app.post('/api/tasks',status_code=202)
    def start(body: TaskRequest,request: Request):
        if access is not None and body.parent_id:
            access.check(request,body.parent_id)
        try:
            run_id=request.app.state.manager.start(body)
            if access is not None: access.record(request,run_id)
            return {'run_id':run_id}
        except (ValueError,KeyError) as exc: raise HTTPException(422,str(exc)) from None
        except RuntimeError as exc: raise HTTPException(429,str(exc)) from None

    @app.get('/api/tasks/{run_id}')
    def detail(run_id:str,request:Request):
        if access is not None: access.check(request,run_id)
        try: return activity(request.app.state.manager,run_id)
        except KeyError: raise HTTPException(404,'任务不存在') from None

    @app.get('/api/tasks/{run_id}/events')
    def events(run_id:str, request:Request):
        if access: access.check(request, run_id)
        mgr=request.app.state.manager
        try: mgr.directory(run_id)
        except KeyError: raise HTTPException(404, '任务不存在') from None
        from sse_starlette.sse import EventSourceResponse
        from server.events import stream_events
        last=request.headers.get('last-event-id','0')
        if not last.isascii() or not last.isdecimal() or len(last)>18:
            raise HTTPException(400, '无效事件编号')
        return EventSourceResponse(stream_events(mgr,run_id,int(last),request))

    @app.get('/api/tasks/{run_id}/view')
    def workbench_view(run_id:str,request:Request):
        if access is not None: access.check(request,run_id)
        from pipeline.view import build
        mgr=request.app.state.manager
        try:
            directory=mgr.directory(run_id)
            # Capture status before reading artifacts. If the run finishes during
            # build(), keep this response running so the UI fetches a final view.
            state=mgr.status(run_id)
            result=build(directory)
            result.update(status=state['status'],mode='回放' if state.get('task',{}).get('mode')=='replay' else '实况',
                          task=state.get('task'),current_stage=state.get('current_stage'))
            result['questions']=activity(mgr,run_id)['questions']
            return public(result)
        except KeyError: raise HTTPException(404,'任务不存在') from None
        except (FileNotFoundError,ValueError): raise HTTPException(409,'正在准备任务资料，请稍后重试') from None
