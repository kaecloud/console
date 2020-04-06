# -*- coding: utf-8 -*-

import json
import yaml
from addict import Dict
from sqlalchemy import event, DDL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.utils import cached_property

from console.ext import db
from console.libs.utils import logger
from console.models.base import BaseModelMixin
from kaelib.spec import app_specs_schema


action_list = [
    "get",
    "update",
    "create",
    "delete",
    "build",
    "getConfig",
    "updateConfig",
    "getSecret",
    "updateSecret",
    "deploy",
    "renew",
    "rollback",
    "restartContainer",
    "enterContainer",
]

role_app_association = db.Table('role_app_association',
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'), primary_key=True)
    db.Column('app_id', db.Integer, db.ForeignKey('app.id'), primary_key=True),
)

class Role(BaseModelMixin):
    __tablename__ = "role"
    name = db.Column(db.CHAR(64), nullable=False, unique=True)
    apps = db.relationship('App', secondary=role_app_association,
                           backref=db.backref('roles', lazy='dynamic'), lazy='dynamic')

    # actions is a json with the following format:
    #   ["get", "deplopy", "getConfig"],
    # if actions is an empty list, it means allows all actions
    actions = db.Column(db.Text)

    users = db.relationship('UserRoleBinding', backref='role', lazy='dynamic')
    groups = db.relationship('GroupRoleBinding', backref='role', lazy='dynamic')


    def __str__(self):
        return self.name

    @classmethod
    def get_by_name(cls, name):
        return cls.query.filter_by(name=name).first()

    def action_list(self):
        return json.loads(self.actions)

    def include_all_apps(self):
        return len(self.apps) == 0

    def include_all_actions(self):
        return len(self.action_list) == 0


class UserRoleBinding(BaseModelMixin):
    __tablename__ = "user_role_binding"
    username = db.Column(db.CHAR(128), nullable=False)
    role = db.Column(db.Integer, db.ForeignKey('role.id'))

    def __str__(self):
        return "UserRoleBinding: {} -> {}".format(self.username, self.role.name)

    @classmethod
    def get_roles_by_name(cls, username):
        l = cls.query.filter_by(username=username)
        return [binding.role for binding in l]


class GroupRoleBinding(BaseModelMixin):
    __tablename__ = "group_role_binding"
    username = db.Column(db.CHAR(128), nullable=False)
    role = db.Column(db.Integer, db.ForeignKey('role.id'))

    def __str__(self):
        return "GroupRoleBinding: {} -> {}".format(self.username, self.role.name)

    @classmethod
    def get_roles_by_name(cls, username):
        l = cls.query.filter_by(username=username)
        return [binding.role for binding in l]