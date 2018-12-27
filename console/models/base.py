# coding: utf-8

import sqlalchemy.orm.exc
import sqlalchemy.types as types
from datetime import datetime
from flask_sqlalchemy import sqlalchemy as sa
from sqlalchemy import inspect

from console.ext import db
from console.libs.jsonutils import Jsonized
from console.libs.utils import logger


class BaseModelMixin(db.Model, Jsonized):

    __abstract__ = True

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    created = db.Column(db.DateTime, server_default=sa.sql.func.now())
    updated = db.Column(db.DateTime, default=sa.sql.func.now(), onupdate=sa.sql.func.now())

    @classmethod
    def create(cls, **kwargs):
        b = cls(**kwargs)
        db.session.add(b)
        db.session.commit()
        return b

    @classmethod
    def get(cls, id):
        return cls.query.get(id)

    @classmethod
    def get_multi(cls, ids):
        return [cls.get(i) for i in ids]

    mget = get_multi

    @classmethod
    def get_all(cls, start=0, limit=None):
        q = cls.query.order_by(cls.id.desc())
        if not any([start, limit]):
            return q.all()
        return q[start:start + limit]

    def update(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

        db.session.add(self)
        db.session.commit()
        return self

    def delete(self):
        try:
            db.session.delete(self)
            db.session.commit()
        except sqlalchemy.orm.exc.ObjectDeletedError:
            db.session.rollback()
            logger.warn('Error during deleting: Object %s already deleted', self)

    def save(self):
        db.session.add(self)
        db.session.commit()
        return self

    def __eq__(self, other):
        return isinstance(other, self.__class__) and self.id == other.id

    def __hash__(self):
        return hash((self.__class__, self.id))

    def to_dict(self):
        return {c.key: getattr(self, c.key) for c in inspect(self).mapper.column_attrs}


class Enum34(types.TypeDecorator):
    impl = types.CHAR(20)

    def __init__(self, enum_class, *args, **kwargs):
        super(Enum34, self).__init__(*args, **kwargs)
        self.enum_class = enum_class

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value not in self.enum_class:
            raise ValueError("'%s' is not a valid enum value" % repr(value))
        return value.value

    def process_result_value(self, value, dialect):
        if value is not None:
            return self.enum_class(value)
        return None


