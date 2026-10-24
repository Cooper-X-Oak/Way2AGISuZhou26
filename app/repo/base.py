"""Repositories share the caller's Session and never commit implicitly."""
from sqlalchemy import select


def page_size(limit):
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    return limit


class Repository:
    def __init__(self, session):
        self.session = session

    def add(self, entity):
        self.session.add(entity)
        self.session.flush()
        return entity

    def get(self, model, identity):
        return self.session.get(model, identity)

    def rows(self, statement):
        return list(self.session.scalars(statement))

    def page(self, model, statement=None, *, cursor=None, limit=100):
        statement = select(model) if statement is None else statement
        if cursor is not None:
            statement = statement.where(model.id > cursor)
        return self.rows(statement.order_by(model.id).limit(page_size(limit)))
