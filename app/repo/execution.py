from sqlalchemy import func, select
from app.domain.models import LlmCall, Task, Visit
from app.repo.base import Repository


class ExecutionRepository(Repository):
    def tasks(self, run_id, *, assignee_id=None, status=None):
        query = select(Task).where(Task.run_id == run_id)
        for column, value in ((Task.assignee_id, assignee_id), (Task.status, status)):
            if value is not None:
                query = query.where(column == value)
        return self.rows(query.order_by(Task.week, Task.day, Task.seq, Task.id))

    def visits(self, task_id):
        return self.rows(select(Visit).where(Visit.task_id == task_id).order_by(Visit.created_at, Visit.id))

    def llm_calls(self, run_id):
        return self.rows(select(LlmCall).where(LlmCall.run_id == run_id).order_by(LlmCall.id))

    def usage(self, run_id):
        row = self.session.execute(select(func.count(LlmCall.id),
            func.coalesce(func.sum(LlmCall.tokens_in), 0),
            func.coalesce(func.sum(LlmCall.tokens_out), 0),
            func.coalesce(func.sum(LlmCall.latency_ms), 0)).where(LlmCall.run_id == run_id)).one()
        return dict(zip(("calls", "tokens_in", "tokens_out", "latency_ms"), row))
