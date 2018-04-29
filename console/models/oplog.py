# -*- coding: utf-8 -*-

import enum
import sqlalchemy
from datetime import datetime

from console.ext import db
from console.libs.datastructure import purge_none_val_from_dict
from console.models.base import BaseModelMixin, Enum34


class OPType(enum.Enum):
    REGISTER_RELEASE = 'register_release'
    DEPLOY_APP = "deploy_app"
    UPGRADE_APP = "upgrade_app"


class OPLog(BaseModelMixin):

    __tablename__ = 'operation_log'
    user_id = db.Column(db.Integer, nullable=False, default=0, index=True)
    appname = db.Column(db.CHAR(64), nullable=False, default='', index=True)
    sha = db.Column(db.CHAR(64), nullable=False, default='', index=True)
    action = db.Column(Enum34(OPType))
    content = db.Column(db.JSON)

    @classmethod
    def get_by(cls, **kwargs):
        '''
        query operation logs, all fields could be used as query parameters
        '''
        purge_none_val_from_dict(kwargs)
        sha = kwargs.pop('sha', None)
        limit = kwargs.pop('limit', 200)
        time_window = kwargs.pop('time_window', None)

        filters = [getattr(cls, k) == v for k, v in kwargs.items()]

        if sha:
            if len(sha) < 7:
                raise ValueError('minimum sha length is 7')
            filters.append(cls.sha.like('{}%'.format(sha)))

        if time_window:
            left, right = time_window
            left = left or datetime.min
            right = right or datetime.now()
            filters.extend([cls.created >= left, cls.created <= right])

        return cls.query.filter(sqlalchemy.and_(*filters)).order_by(cls.id.desc()).limit(limit).all()

    @classmethod
    def create(cls, user_id=None, appname=None,
               sha=None, action=None, content=None):
        op_log = cls(user_id=user_id,
                     appname=appname, sha=sha, action=action, content=content)
        db.session.add(op_log)
        db.session.commit()
        return op_log

    @property
    def verbose_action(self):
        return self.action.name

    @property
    def short_sha(self):
        return self.sha and self.sha[:7]
