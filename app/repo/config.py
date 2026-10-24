from sqlalchemy import select
from app.domain.models import AppUser, ParamSet, Rule, Workspace
from app.repo.base import Repository


class ConfigRepository(Repository):
    def workspaces(self):
        return self.rows(select(Workspace).order_by(Workspace.id))

    def users(self, workspace_id):
        return self.rows(select(AppUser).where(AppUser.workspace_id == workspace_id).order_by(AppUser.id))

    def param_set(self, version):
        return self.session.scalar(select(ParamSet).where(ParamSet.version == version))

    def rules(self, *, version=None, kind=None, active=True):
        query = select(Rule)
        for column, value in ((Rule.version, version), (Rule.kind, kind), (Rule.active, active)):
            if value is not None:
                query = query.where(column == value)
        return self.rows(query.order_by(Rule.version, Rule.id))
