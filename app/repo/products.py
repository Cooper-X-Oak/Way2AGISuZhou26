from sqlalchemy import select
from app.domain.models import Product, ProfileFact, ProfileVersion, SourceDoc
from app.repo.base import Repository


class ProductRepository(Repository):
    def products(self, *, workspace_id=None, cursor=None, limit=100):
        query = select(Product)
        if workspace_id is not None:
            query = query.where(Product.workspace_id == workspace_id)
        return self.page(Product, query, cursor=cursor, limit=limit)

    def product(self, product_id):
        return self.get(Product, product_id)

    def versions(self, product_id):
        return self.rows(select(ProfileVersion).where(ProfileVersion.product_id == product_id)
                         .order_by(ProfileVersion.version))

    def version(self, product_id, version):
        return self.session.scalar(select(ProfileVersion).where(
            ProfileVersion.product_id == product_id, ProfileVersion.version == version))

    def facts(self, version_id, *, kind=None):
        query = select(ProfileFact).where(ProfileFact.version_id == version_id)
        if kind is not None:
            query = query.where(ProfileFact.kind == kind)
        return self.rows(query.order_by(ProfileFact.kind, ProfileFact.key))

    def source(self, source_doc_id):
        return self.get(SourceDoc, source_doc_id)
