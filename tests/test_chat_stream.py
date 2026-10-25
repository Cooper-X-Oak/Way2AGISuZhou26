import json
import socket
import threading
import time
import httpx
import uvicorn
from fastapi.testclient import TestClient
from server.app import create_app
from server.public import create_public_app
from test_chat_library import Manager, BODY


def test_real_http_delivers_operation_before_final_model_reply(tmp_path, monkeypatch):
    import server.chat as chat
    release=threading.Event()
    calls=[]
    def model(*args, **kwargs):
        calls.append(1)
        if len(calls)==1:
            chat.emit('text_delta',text='先分析')
            return {'role':'assistant','tool_calls':[{'id':'x','function':{'name':'execute_market_task','arguments':json.dumps({'request':'分析门店','brief':'推广面包','plan':'查找候选'})}}]}
        assert release.wait(8), 'client never received early operation'
        chat.emit('text_delta',text='已提交')
        return {'content':'已提交'}
    monkeypatch.setattr(chat,'completion',model)
    manager=Manager(tmp_path)
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen()
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(manager),log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+5
        while not server.started and time.monotonic()<deadline:time.sleep(.01)
        events=[]
        with httpx.stream('POST',f'http://127.0.0.1:{port}/api/chat/stream',json=BODY,timeout=10) as response:
            assert response.status_code==200
            for line in response.iter_lines():
                if not line.startswith('data:'):continue
                event=json.loads(line[5:]);events.append(event)
                if event['type']=='operation':
                    assert not release.is_set()
                    assert event['run_id']=='a'*32
                    assert list((tmp_path/'conversations').glob('*.json'))
                    release.set()
        assert [e['text'] for e in events if e['type']=='text_delta']==['先分析','已提交']
        assert events[-1]['type']=='done'
        assert len(manager.started)==1
    finally:
        release.set();server.should_exit=True;thread.join(5);sock.close()


def test_public_stream_checks_ownership_and_reports_model_failure(tmp_path,monkeypatch):
    import server.chat as chat
    from fastapi import HTTPException
    monkeypatch.setattr(chat,'completion',lambda *a,**k: (_ for _ in ()).throw(HTTPException(502,'模型超时')))
    with TestClient(create_public_app(tmp_path,Manager(tmp_path)),base_url='https://demo.test') as client:
        assert client.post('/api/chat/stream',json={**BODY,'conversation_id':'b'*32}).status_code==404
        assert client.get('/api/tasks/'+'b'*32+'/events').status_code==404
        response=client.post('/api/chat/stream',json=BODY)
        events=[json.loads(line[5:]) for line in response.text.splitlines() if line.startswith('data:')]
        assert events[-1]=={'type':'error','message':'模型超时'}
        assert not chat.BUSY


def test_provider_stream_assembles_split_tool_arguments_and_only_emits_body(monkeypatch):
    import server.chat as chat
    for k in ('LLM_BASE_URL','LLM_API_KEY','LLM_MODEL'):monkeypatch.setenv(k,'http://test' if k=='LLM_BASE_URL' else 'test')
    chunks=[{'content':'你好','reasoning_content':'private'}, {'tool_calls':[{'index':0,'id':'call1','function':{'name':'update_document','arguments':'{"brief":'}}]}, {'tool_calls':[{'index':0,'function':{'arguments':'"资料"}'}}]}]
    def transport(request):
        assert json.loads(request.content)['stream'] is True
        text=''.join('data: '+json.dumps({'choices':[{'delta':d}]})+'\n\n' for d in chunks)+'data: [DONE]\n\n'
        return httpx.Response(200,text=text)
    original=httpx.Client
    monkeypatch.setattr(chat.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(transport),**kwargs))
    events=[];token=chat.EMIT.set(events.append)
    try:result=chat.completion([],chat.TOOLS)
    finally:chat.EMIT.reset(token)
    assert result['content']=='你好'
    assert json.loads(result['tool_calls'][0]['function']['arguments'])=={'brief':'资料'}
    assert events==[{'type':'model_start'},{'type':'text_delta','text':'你好'}]

def test_tool_progress_is_emitted_before_tool_returns():
    from pipeline.agent_loop import Tool, run_agent
    events=[]
    def tool():
        assert events==[('tool_start',{'tool':'probe'})]
        return {'ok':True}
    replies=iter([
        {'choices':[{'message':{'tool_calls':[{'id':'t','function':{'name':'probe','arguments':'{}'}}]}}]},
        {'choices':[{'message':{'content':'{}'}}]},
    ])
    run_agent('system','user',[Tool('probe','probe',{'type':'object','properties':{}},tool)],post=lambda body:next(replies),validate=lambda result:[],on_progress=lambda kind,**data:events.append((kind,data)))
    assert events[-1]==('tool_done',{'tool':'probe','ok':True})
