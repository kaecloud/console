# -*- coding: utf-8 -*-

from datetime import datetime

import enum
import sqlalchemy
from sqlalchemy import inspect

from console.ext import db
from console.libs.datastructure import purge_none_val_from_dict
from console.models.base import BaseModelMixin, Enum34
from console.models.user import User


class OPType(enum.Enum):
    REGISTER_RELEASE = 'register_release'
    UPDATE_RELEASE = 'update_release'
    DEPLOY_APP = "deploy_app"
    UNDEPLOY_APP = "undeploy_app"
    DEPLOY_APP_CANARY = "deploy_app_canary"
    UNDEPLOY_APP_CANARY = "undeploy_app_canary"
    UPGRADE_APP = "upgrade_app"
    SCALE_APP = "scale_app"
    DELETE_APP = "delete_app"
    ROLLBACK_APP = "rollback_app"
    RENEW_APP = "renew_app"


class OPLog(BaseModelMixin):

    __tablename__ = 'operation_log'
    user_id = db.Column(db.Integer, nullable=False, default=0, index=True)
    app_id = db.Column(db.Integer, nullable=False, default=0, index=True)
    appname = db.Column(db.CHAR(64), nullable=False, default='', index=True)
    tag = db.Column(db.CHAR(64), nullable=False, default='', index=True)
    cluster = db.Column(db.CHAR(64), nullable=False, default='')
    action = db.Column(Enum34(OPType))
    content = db.Column(db.Text)

    @classmethod
    def get_by(cls, **kwargs):
        '''
        query operation logs, all fields could be used as query parameters
        '''
        purge_none_val_from_dict(kwargs)
        limit = kwargs.pop('limit', 100)
        time_window = kwargs.pop('time_window', None)

        filters = [getattr(cls, k) == v for k, v in kwargs.items()]

        if time_window:
            left, right = time_window
            left = left or datetime.min
            right = right or datetime.now()
            filters.extend([cls.created >= left, cls.created <= right])

        return cls.query.filter(sqlalchemy.and_(*filters)).order_by(cls.id.desc()).limit(limit).all()

    @classmethod
    def create(cls, user_id=None, app_id=None, appname=None,
               tag=None, action=None, content=None, cluster=''):
        op_log = cls(user_id=user_id, app_id=app_id, cluster=cluster,
                     appname=appname, tag=tag, action=action, content=content)
        db.session.add(op_log)
        db.session.commit()
        return op_log

    @property
    def verbose_action(self):
        return self.action.name

    def to_dict(self):
        dic = {c.key: getattr(self, c.key)
               for c in inspect(self).mapper.column_attrs
               if c.key not in ('user_id', 'app_id')}
        user = User.get_by_id(self.user_id)
        dic['username'] = user.nickname
        dic['action'] = self.action.name
        return dic

