from sqlalchemy import and_, or_, select
from app.domain.models import (Artifact, Attempt, Clarification, Event, Gate,
                               Poi, ProfileVersion, Run, StageRun, TerminalPick)
from app.repo.base import Repository, page_size


class RunRepository(Repository):
    def runs(self, *, status=None, area=None, product=None, workspace_id=None,
             mode=None, cursor=None, limit=100):
        query = select(Run)
        for column, value in ((Run.status, status), (Run.area_id, area),
                              (Run.workspace_id, workspace_id), (Run.mode, mode)):
            if value is not None:
                query = query.where(column == value)
        if product is not None:
            query = query.join(ProfileVersion).where(ProfileVersion.product_id == product)
        return self.page(Run, query, cursor=cursor, limit=limit)

    def run(self, run_id):
        return self.get(Run, run_id)

    def stages(self, run_id):
        return self.rows(select(StageRun).where(StageRun.run_id == run_id)
                         .order_by(StageRun.stage, StageRun.revision))

    def attempts(self, stage_run_id):
        return self.rows(select(Attempt).where(Attempt.stage_run_id == stage_run_id).order_by(Attempt.n))

    def events(self, run_id, *, after_seq=0, limit=100):
        return self.rows(select(Event).where(Event.run_id == run_id, Event.seq > after_seq)
                         .order_by(Event.seq).limit(page_size(limit)))

    def gates(self, run_id, *, stage=None, status=None):
        query = select(Gate).where(Gate.run_id == run_id)
        for column, value in ((Gate.stage, stage), (Gate.status, status)):
            if value is not None:
                query = query.where(column == value)
        return self.rows(query.order_by(Gate.id))

    def clarifications(self, run_id):
        return self.rows(select(Clarification).where(Clarification.run_id == run_id)
                         .order_by(Clarification.asked_at, Clarification.id))

    def artifacts(self, run_id, *, name=None):
        query = select(Artifact).where(Artifact.run_id == run_id)
        if name is not None:
            query = query.where(Artifact.name == name)
        return self.rows(query.order_by(Artifact.id))

    def terminals(self, run_id, *, grade=None, channel=None, cell=None,
                  sort="score_desc", cursor=None, limit=100):
        """Keyset cursor is the last pick ID; filters and sort must stay fixed.

        Projections are immutable for a completed run. Sorting ties always use ID.
        Invalid / cross-run / filtered-out cursors fail instead of silently skipping.
        """
        columns = {"score_desc": (TerminalPick.score, True),
                   "score_asc": (TerminalPick.score, False),
                   "grade": (TerminalPick.grade, False),
                   "channel": (TerminalPick.channel_id, False),
                   "cell": (TerminalPick.cell_key, False),
                   "id": (TerminalPick.id, False)}
        if sort not in columns:
            raise ValueError("unsupported terminal sort")
        column, descending = columns[sort]
        query = select(TerminalPick).where(TerminalPick.run_id == run_id)
        for field, value in ((TerminalPick.grade, grade), (TerminalPick.channel_id, channel),
                             (TerminalPick.cell_key, cell)):
            if value is not None:
                query = query.where(field == value)
        if cursor is not None:
            anchor = self.session.scalar(query.where(TerminalPick.id == cursor))
            if anchor is None:
                raise ValueError("cursor does not belong to this terminal query")
            value = getattr(anchor, column.key)
            comparison = column < value if descending else column > value
            query = query.where(or_(comparison, and_(column == value, TerminalPick.id > cursor)))
        return self.rows(query.order_by(column.desc() if descending else column.asc(), TerminalPick.id)
                         .limit(page_size(limit)))

    def trace(self, run_id, poi_id):
        """Queryable projection plus source versions; artifact payloads remain blobs."""
        run = self.run(run_id)
        pick = self.session.scalar(select(TerminalPick).where(
            TerminalPick.run_id == run_id, TerminalPick.poi_id == poi_id))
        if run is None or pick is None:
            return None
        return {"run": run, "terminal": pick, "poi": self.get(Poi, poi_id),
                "product_version": self.get(ProfileVersion, run.product_version_id),
                "artifacts": self.artifacts(run_id)}
