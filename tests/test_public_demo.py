import threading
from fastapi.testclient import TestClient

from server.public import COOKIE, SessionAccess, create_public_app


class Manager:
    def __init__(self):self.lock=threading.RLock();self.runs={}
    def start(self,body):
        run_id=f'{len(self.runs)+1:032x}'
        self.runs[run_id]={'run_id':run_id,'status':'completed','task':{'created_at':run_id,'message':body.input}}
        return run_id
    def status(self,run_id):return self.runs[run_id]
    def close(self):pass


BODY={'area':'shishan','profile':'xinxian-huoquan','input':'周边3公里推广','mode':'live'}


def test_visitors_cannot_list_read_or_continue_each_others_tasks(tmp_path):
    app=create_public_app(tmp_path,Manager())
    with TestClient(app,base_url='https://demo.test') as alice,TestClient(app,base_url='https://demo.test') as bob:
        response=alice.get('/')
        assert response.status_code==200
        assert 'HttpOnly' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']
        assert 'no-store' in response.headers['cache-control']
        created=alice.post('/api/tasks',json=BODY)
        assert created.status_code==202
        run_id=created.json()['run_id']
        assert len(alice.get('/api/tasks').json())==1
        assert bob.get('/api/tasks').json()==[]
        for suffix in ('','/view'):
            assert bob.get('/api/tasks/'+run_id+suffix).status_code==404
        assert bob.post('/api/tasks',json={**BODY,'parent_id':run_id}).status_code==404
        assert alice.post('/api/tasks',json={**BODY,'parent_id':run_id}).status_code==202
        assert SessionAccess(tmp_path).owners==app.state.public_access.owners
        assert COOKIE in alice.cookies


def test_public_surface_does_not_expose_local_files_legacy_api_or_cross_site_posts(tmp_path):
    with TestClient(create_public_app(tmp_path,Manager()),base_url='https://demo.test') as client:
        for path in ('/.env','/docs','/openapi.json','/api/runs','/api/runs/'+32*'a'+'/artifacts/inputs.json','/session-owners.json'):
            assert client.get(path).status_code==404
        assert client.post('/api/tasks',json=BODY,headers={'Origin':'https://unrelated.test'}).status_code==403
        assert client.post('/api/tasks',json=BODY,headers={'Origin':'https://demo.test'}).status_code==202
        for _ in range(9):assert client.post('/api/tasks',json=BODY).status_code==202
        assert client.post('/api/tasks',json=BODY).status_code==429
