import json
import threading
from copy import deepcopy
from fastapi.testclient import TestClient
from server.public import create_public_app
from server.library import apply_selection
from pipeline.common import task_context
from pipeline.profiles import load

class Manager:
    def __init__(self,root):self.root=root;self.runs={};self.lock=threading.RLock();self.started=[]
    def start(self,body):self.started.append(body);return 'a'*32
    def close(self):pass
    def status(self,run_id):return {"status":"completed"}
    def directory(self,run_id):return self.root/"runs"/run_id

BODY={'area':'shishan','profile':'bread-trial','input':'你好'}

def test_chat_does_not_run_pipeline_and_document_is_owned_and_persistent(tmp_path,monkeypatch):
    import server.chat
    manager=Manager(tmp_path)
    replies=iter([
        {'role':'assistant','content':'你好，可以先聊聊你的目标。'},
        {'role':'assistant','content':None,'tool_calls':[{'id':'u1','type':'function','function':{'name':'update_document','arguments':json.dumps({'brief':'推广面包，预算待确认','plan':'先了解便利店','market_judgment':'假设：便利店可能适合，待验证'})}}]},
        {'role':'assistant','content':'已更新文档，尚未执行分析。'},
        {'role':'assistant','content':None,'tool_calls':[{'id':'e1','type':'function','function':{'name':'execute_market_task','arguments':json.dumps({'request':'周边3公里推广面包','brief':'推广面包，预算待确认','plan':'先了解便利店'})}}]},
        {'role':'assistant','content':'分析已提交。'},
    ])
    monkeypatch.setattr(server.chat,'completion',lambda *a,**k:next(replies))
    app=create_public_app(tmp_path,manager)
    with TestClient(app,base_url='https://demo.test') as alice,TestClient(app,base_url='https://demo.test') as bob:
        first=alice.post('/api/chat',json=BODY);assert first.status_code==200
        cid=first.json()['conversation_id'];assert not manager.started
        second=alice.post('/api/chat',json={**BODY,'conversation_id':cid,'input':'先记录计划，不执行'})
        assert second.json()['document']['plan']=='先了解便利店' and not manager.started
        assert len(second.json()['changes'])==1
        assert alice.get('/api/chat/'+cid).json()['document']==second.json()['document']
        assert bob.get('/api/chat/'+cid).status_code==404
        assert bob.post('/api/chat',json={**BODY,'conversation_id':cid}).status_code==404
        third=alice.post('/api/chat',json={**BODY,'conversation_id':cid,'input':'现在开始'})
        assert third.json()['run_id']=='a'*32
        assert third.json()['messages'][-1]['run_id']=='a'*32
        assert manager.started[0].document['plan']=='先了解便利店'
        assert len(third.json()['messages'])==6
        reviewed=alice.post('/api/chat',json={**BODY,'conversation_id':cid,'review':True})
        assert reviewed.status_code==200
        assert '待验证' in reviewed.json()['document']['market_judgment']
        assert reviewed.json()['changes'][-1]['source_run']=='a'*32
        assert len(manager.started)==1
        again=alice.post('/api/chat',json={**BODY,'conversation_id':cid,'review':True})
        assert again.status_code==200 and len(again.json()['messages'])==7

def test_library_snapshot_changes_product_facts_and_routes_skills_without_mutating_template():
    original=load('bread-trial');inputs={'profile':deepcopy(original),'site':{},'coverage':{'site':{}},'request':'推广新面包'}
    selection={'product':{'name':'测试全麦面包','facts':'每袋两片','source':'用户产品说明'},'area':{'name':'狮山北片','radius_m':1200},'skills':[{'name':'先盘点','instructions':'先盘点已有候选，缺少再搜索','stages':['L1D']}]}
    apply_selection(inputs,selection)
    assert inputs['profile']['product']['name']=='测试全麦面包'
    assert all(x['fact']=='每袋两片' for x in inputs['profile']['selling_points'])
    assert original['product']['name']!='测试全麦面包'
    assert task_context(inputs,'L1D')['skills'][0]['name']=='先盘点'
    assert task_context(inputs,'L4')['skills']==[]
    assert inputs['library_documents']['library-product']=='每袋两片\n资料来源：用户产品说明'


def test_document_tool_can_continue_to_execution_in_the_same_turn(tmp_path, monkeypatch):
    import server.chat
    manager=Manager(tmp_path)
    calls=[]
    def completion(messages, tools=None):
        calls.append(tools)
        if len(calls)==1:
            name='update_document';args={'brief':'周边3公里推广面包','plan':'先分析候选'}
        elif len(calls)==2:
            assert tools is server.chat.TOOLS
            assert messages[-1]['role']=='tool'
            name='execute_market_task';args={'request':'周边3公里推广面包','brief':'周边3公里推广面包','plan':'先分析候选'}
        else:
            assert tools is None
            return {'role':'assistant','content':'任务已提交。'}
        return {'role':'assistant','content':None,'tool_calls':[{'id':str(len(calls)),'type':'function',
                'function':{'name':name,'arguments':json.dumps(args)}}]}
    monkeypatch.setattr(server.chat,'completion',completion)
    with TestClient(create_public_app(tmp_path,manager),base_url='https://demo.test') as client:
        response=client.post('/api/chat',json={**BODY,'input':'现在执行分析'})
        assert response.status_code==200
        assert response.json()['run_id']=='a'*32
        assert len(response.json()['messages'])==2
        assert len(manager.started)==1
        assert len(calls)==3
