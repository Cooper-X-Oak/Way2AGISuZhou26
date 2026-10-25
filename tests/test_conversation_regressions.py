"""Regressions from actual workbench messages, without external model calls."""
import pytest

from pipeline.l0 import facts
from pipeline.profiles import load, parameters
from pipeline.__main__ import prepare_data


@pytest.mark.parametrize('message,expected', [
    ('把半径改成 2 公里，3 个地推，做 6 周', (2000, 3, 6)),
    ('希望覆盖 3km，2个人，4周', (3000, 2, 4)),
    ('推荐 3 公里内的供货方，2个人，4周', (3000, 2, 4)),
    ('3 公里，2个地推，做4周', (3000, 2, 4)),
    ('周边3公里，2个人，4周\n追加要求：推荐 2 公里内的供货方', (2000, 2, 4)),
    ('周边3公里，2个地推，做4周\n追加要求：把半径改成 2 公里，3 个地推，做 6 周', (2000, 3, 6)),
    ('周边3公里，2个人，4周\n追加要求：改为3人，6周', (3000, 3, 6)),
])
def test_actual_user_phrases(message, expected):
    params=parameters()
    _,coverage=prepare_data(params,'shishan')
    supplied,_=facts(dict(request=message,site=coverage['site'],profile=load('xinxian-huoquan'),params=params))
    assert tuple(supplied[k] for k in ('radius_m','delivery_people','weeks'))==expected


def test_blocked_task_numeric_answer_and_correction(tmp_path, monkeypatch):
    import server.runs
    import pipeline.demo
    from pipeline.io import write
    from server.tasks import TaskRequest
    from server.runs import RunManager
    captured=[]
    def execute(inputs,root,run_id,observer,**kwargs):
        captured.append(inputs)
        directory=root/'runs'/run_id
        write(directory/'inputs.json',{k:v for k,v in inputs.items() if k!='judge'})
        supplied,_=facts(inputs)
        write(directory/'L0.json',{**supplied,'questions':['请补充 radius_m'] if 'radius_m' not in supplied else []})
        # A run that failed before POI persistence can still receive a reply.
        state=dict(run_id=run_id,status='blocked',completed_stages=[])
        write(directory/'status.json',state)
        observer(dict(event='run_done',payload=state))
    monkeypatch.setattr(server.runs,'execute',execute)
    monkeypatch.setattr(pipeline.demo,'live_ready',lambda:[])
    manager=RunManager(tmp_path)
    try:
        parent=None
        for message in ['2个人做4周的推广任务','400','把半径改成 2 公里，3 个地推，做 6 周']:
            parent=manager.start(TaskRequest(area='shishan',profile='xinxian-huoquan',input=message,parent_id=parent))
            assert manager.runs[parent]['done'].wait(15)
        second,_=facts(captured[1]);third,_=facts(captured[2])
        assert second['radius_m']==400
        assert third['radius_m']==2000
        assert third['delivery_people']==3
        assert third['weeks']==6
        assert captured[1]['task']['message']=='400'
    finally:
        manager.close()
