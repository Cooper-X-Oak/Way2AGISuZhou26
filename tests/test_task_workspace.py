"""Exercise the task entry, genuine recorded tool events, recovery and follow-up context."""
import socket
import time

from fastapi.testclient import TestClient

from pipeline.demo import CASE
from pipeline.io import read, write
from server.app import create_app
from server.runs import RunManager


def test_recorded_task_activity_and_restart(tmp_path, monkeypatch):
    def no_network(*a, **kw): raise AssertionError('Recorded tasks must not use the network')
    monkeypatch.setattr(socket,'getaddrinfo',no_network)
    manager=RunManager(tmp_path)
    with TestClient(create_app(manager)) as client:
        body=dict(area=CASE['area'],profile=CASE['profile'],input=CASE['request'],mode='replay')
        assert client.post('/api/tasks',json={**body,'profile':'bread-trial'}).status_code==422
        assert client.post('/api/tasks',json={**body,'input':'changed task'}).status_code==422
        response=client.post('/api/tasks',json=body)
        assert response.status_code==202,response.text
        run=response.json()['run_id']
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            data=client.get(f'/api/tasks/{run}').json()
            if data['state']['status'] in ('completed','blocked','failed'): break
            time.sleep(.1)
        assert data['state']['status']=='completed',data['state']
        tools=[e for e in data['events'] if e['kind']=='tool']
        assert len(tools)==6
        assert tools[0]['result']['by_channel']['gym_channel']==0
        assert tools[-1]['result']['by_channel']['gym_channel']==24
        assert any(e.get('args',{}).get('types')=='0803|0804|0805' for e in tools)
        assert all('note' not in e for e in data['events'])
        assert data['artifacts']['discovery']['agent']['amap_calls']==0
        assert len(data['artifacts']['terminals']['items'])==14
        assert all(t['name'] for t in data['artifacts']['terminals']['items'])
        assert client.get('/api/tasks').json()[0]['task']['message']==CASE['request']
    restored=RunManager(tmp_path)
    with TestClient(create_app(restored)) as client:
        assert client.get(f'/api/tasks/{run}').json()['state']['status']=='completed'
        assert client.get('/api/tasks').json()[0]['run_id']==run


def test_followup_retains_request_and_discovered_pois(tmp_path,monkeypatch):
    import server.runs
    import pipeline.demo
    from server.tasks import TaskRequest
    captured=[]
    def execute(inputs,root,run_id,observer,**kwargs):
        captured.append(inputs)
        directory=root/'runs'/run_id;directory.mkdir(parents=True)
        write(directory/'inputs.json',{k:v for k,v in inputs.items() if k!='judge'})
        write(directory/'pois.json',inputs['pois']+[dict(id='synthetic-discovered',name='测试候选',lat=31,lon=120)])
        state=dict(run_id=run_id,status='completed',completed_stages=[])
        write(directory/'status.json',state)
        observer(dict(event='run_done',payload=state))
    monkeypatch.setattr(server.runs,'execute',execute)
    monkeypatch.setattr(pipeline.demo,'live_ready',lambda:[])
    for key,value in (('LLM_BASE_URL','http://model.invalid'),('LLM_API_KEY','test'),('LLM_MODEL','synthetic-model')):monkeypatch.setenv(key,value)
    manager=RunManager(tmp_path)
    try:
        first=manager.start(TaskRequest(area='shishan',profile='bread-trial',input='周边3公里，2个人，4周'))
        assert manager.runs[first]['done'].wait(15)
        second=manager.start(TaskRequest(area='shishan',profile='bread-trial',input='继续查找便利店',parent_id=first))
        assert manager.runs[second]['done'].wait(15)
        assert len(captured)==2
        assert captured[0]['agent_l1d'] is True
        # Live tasks hand every judgment stage to the model; code checks still decide pass/fail.
        assert captured[0]['judge'].label=='llm:synthetic-model'
        assert captured[1]['request']=='周边3公里，2个人，4周\n追加要求：继续查找便利店'
        assert any(p['id']=='synthetic-discovered' for p in captured[1]['pois'])
        assert captured[1]['task']['parent_id']==first
        from pipeline.l1d import prompt
        # The model prompt now receives the actual user request, not just the product label.
        context={**captured[1],'brief':{'radius_m':3000},'l1':{'items':[]}}
        assert '继续查找便利店' in prompt(context)
        from pipeline.common import decide
        class CaptureJudge:
            def decide(self, stage, payload, rules):
                assert payload['task_context']['brief']['request'] == captured[1]['request']
                assert payload['task_context']['previous_round']['run_id'] == first
                assert payload['task_context']['completed_decisions']['l1'] == '按当前目标选渠道'
                return {}
        context.update(judge=CaptureJudge(), l1={'decision':'按当前目标选渠道'})
        for stage in ('L0','L1','L2','L2C','L3','L3P','L4'):
            decide(context, stage, {}, lambda: {}, {})
    finally: manager.close()
