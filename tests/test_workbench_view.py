import json
from pipeline.io import write
from pipeline.profiles import load
from pipeline.view import build, render


def test_workbench_can_show_clarification_and_partial_steps(tmp_path):
    write(tmp_path/'status.json',dict(run_id='test',status='blocked',completed_stages=[],stopped_at='L0'))
    write(tmp_path/'profile.json',load('xinxian-huoquan'))
    write(tmp_path/'L0.json',dict(radius_m=None,delivery_people=None,weeks=None,request='推广任务',questions=['请补充 weeks']))
    write(tmp_path/'coverage.json',dict(site={'lat':31.28,'lon':120.55},radius_m=3000))
    write(tmp_path/'pois.json',[])
    (tmp_path/'agent').mkdir()
    (tmp_path/'agent/steps.jsonl').write_text(json.dumps(dict(timestamp='2026-10-25T00:00:00+00:00',stage='L1D',kind='model',tool_calls=['area_inventory'],note='先盘点现有商户'))+'\n{"unfinished":',encoding='utf-8')
    data=build(tmp_path)
    assert data['status']=='blocked' and data['radius']==3000
    assert data['brief']['request']=='推广任务'
    assert data['brief']['questions']==['请补充 weeks']
    assert data['brief']['product_background']['name']=='洞庭山·新鲜活泉'
    assert data['steps']==[dict(kind='model',calls=['盘点现有商户'],note='')]
    # The model's visible message (not hidden reasoning, which agent_loop never stores) appears in the activity stream.
    assert data['activity']==[dict(t='2026-10-25T00:00:00+00:00',kind='model',stage='L1D',text='决定：盘点现有商户',note='先盘点现有商户')]
    assert data['live'] is None
    page=render(data)
    assert 'height:100dvh' in page and 'overscroll-behavior:contain' in page
