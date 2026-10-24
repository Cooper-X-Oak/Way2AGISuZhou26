from sqlalchemy import func, select
from sqlalchemy.orm import aliased
from app.domain.models import Area, AreaPoi, Capture, GridCell, Poi, PoiClass
from app.repo.base import Repository


class AreaRepository(Repository):
    def areas(self, *, workspace_id=None):
        query = select(Area)
        if workspace_id is not None:
            query = query.where(Area.workspace_id == workspace_id)
        return self.rows(query.order_by(Area.id))

    def area(self, area_id):
        return self.get(Area, area_id)

    def captures(self, area_id):
        return self.rows(select(Capture).where(Capture.area_id == area_id)
                         .order_by(Capture.started_at, Capture.id))

    def cells(self, area_id, *, rule_version=None):
        if rule_version is None:
            rule_version = select(func.max(GridCell.rule_version)).where(
                GridCell.area_id == area_id).scalar_subquery()
        return self.rows(select(GridCell).where(GridCell.area_id == area_id,
                         GridCell.rule_version == rule_version).order_by(GridCell.cell_key))

    def pois(self, area_id, *, role=None, channel=None, cell=None,
             rule_version=None, cursor=None, limit=100):
        query = select(Poi).join(AreaPoi).where(AreaPoi.area_id == area_id)
        if cell is not None:
            query = query.where(AreaPoi.cell_key == cell)
        if role is not None or channel is not None or rule_version is not None:
            classification = select(PoiClass.id).where(PoiClass.poi_id == Poi.id)
            if rule_version is None:
                historical = aliased(PoiClass)
                latest = select(func.max(historical.rule_version)).where(
                    historical.poi_id == PoiClass.poi_id,
                    historical.role == PoiClass.role).correlate(PoiClass).scalar_subquery()
                classification = classification.where(PoiClass.rule_version == latest)
            else:
                classification = classification.where(PoiClass.rule_version == rule_version)
            for column, value in ((PoiClass.role, role), (PoiClass.channel_id, channel)):
                if value is not None:
                    classification = classification.where(column == value)
            query = query.where(classification.exists())
        return self.page(Poi, query, cursor=cursor, limit=limit)

    def classifications(self, poi_id):
        return self.rows(select(PoiClass).where(PoiClass.poi_id == poi_id)
                         .order_by(PoiClass.rule_version, PoiClass.id))
