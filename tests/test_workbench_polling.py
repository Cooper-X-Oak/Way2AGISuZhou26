from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_finish_during_view_build_does_not_cache_partial_view(tmp_path,monkeypatch):
    import pipeline.view
    import server.tasks
    class Manager:
        state='running'
        def directory(self,run_id):return tmp_path
        def status(self,run_id):return {'status':self.state,'current_stage':'L1D','task':{'mode':'live'}}
    manager=Manager()
    def build(directory):
        was_running=manager.state=='running'
        manager.state='completed'
        return {'stages':[{'id':'PACK','done':not was_running}]}
    monkeypatch.setattr(pipeline.view,'build',build)
    monkeypatch.setattr(server.tasks,'activity',lambda *args:{'questions':[]})
    app=FastAPI();app.state.manager=manager;server.tasks.install(app)
    with TestClient(app) as client:
        first=client.get('/api/tasks/test/view').json()
        assert first['status']=='running'
        assert not first['stages'][0]['done']
        final=client.get('/api/tasks/test/view').json()
        assert final['status']=='completed'
        assert final['stages'][0]['done']
