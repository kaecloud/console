# -*- coding: utf-8 -*-

import json
import yaml
from addict import Dict
from sqlalchemy import event, DDL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.utils import cached_property

from console.ext import db, sso
from console.libs.utils import logger
from console.models.base import BaseModelMixin
from kaelib.spec import app_specs_schema


class RBACAction(enum.Enum):
    GET = "get"
    UPDATE = "update"
    CREATE = "create"
    DELETE = "delete"
    DELETE = "build"
    GET_CONFIG = "getConfig"
    UPDATE_CONFIG = "updateConfig"
    GET_SECRET = "getSecret"
    UPDATE_SECRET = "updateSecret"
    DEPLOY = "deploy"
    RENEW = "renew"
    ROLLBACK = "rollback"
    RESTART_CONTAINER = "restartContainer"
    ENTER_CONTAINER = "enterContainer"

    ADMIN = "admin"

_all_action_list = [
    RBACAction.GET,
    RBACAction.UPDATE,
    RBACAction.CREATE,
    RBACAction.DELETE,
    RBACAction.BUILD,
    RBACAction.GET_CONFIG,
    RBACAction.UPDATE_CONFIG,
    RBACAction.GET_SECRET,
    RBACAction.UPDATE_SECRET,
    RBACAction.DEPLOY,
    RBACAction.RENEW,
    RBACAction.ROLLBACK,
    RBACAction.RESTART_CONTAINER,
    RBACAction.ENTER_CONTAINER,

    RBACAction.ADMIN,
]

role_app_association = db.Table('role_app_association',
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'), primary_key=True)
    db.Column('app_id', db.Integer, db.ForeignKey('app.id'), primary_key=True),
)

def check_rbac(app, actions):
    username = g.user["username"]
    group = sso.get_group_by_user(username)

    if group is None:
        return False
    user_roles = UserRoleBinding.get_roles_by_name(username) 
    group_roles = GroupRoleBinding.get_roles_by_id(group.id)

    app_role_names = set(role.name for role in app.roles)
    for role in user_roles:
        if role not in app_role_names:
            continue
        if len(set(actions) - set(role.get_actions)) == 0:
            return True

    for role in group_roles:
        if role not in app_role_names:
            continue
        if len(set(actions) - set(role.get_actions)) == 0:
            return True
    return False


class Role(BaseModelMixin):
    __tablename__ = "role"
    name = db.Column(db.CHAR(64), nullable=False, unique=True)
    apps = db.relationship('App', secondary=role_app_association,
                           backref=db.backref('roles', lazy='dynamic'), lazy='dynamic')

    # actions is a json with the following format:
    #   ["get", "deploy", "getConfig"],
    # if actions is an empty list, it means allows all actions
    actions = db.Column(db.Text)
    # clusters is a json list with the following format:
    # ["cluster1", "cluster2", "cluster3"]
    # if clusters is an empty list, it mains allows all clusters
    clusters = db.Column(db.Text)

    users = db.relationship('UserRoleBinding', backref='role', lazy='dynamic')
    groups = db.relationship('GroupRoleBinding', backref='role', lazy='dynamic')


    def __str__(self):
        return self.name

    @classmethod
    def get_by_name(cls, name):
        return cls.query.filter_by(name=name).first()

    @property
    def action_list(self):
        actions = json.loads(self.actions)
        if len(actions) == 0:
            return _all_action_list
        else:
            return actions

    @property
    def cluster_list(self):
        clusters = json.loads(self.clusters)
        # TODO
        # if len(clusters) == 0:
        #     #TODO
        # else:
        #     return clusters
        return clusters

    @property
    def app_list(self):
        apps = self.apps.all() 
        if len(apps) == 0:
            from console.models.app import App
            return App.get_all()
        else:
            return apps


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
    group_id = db.Column(db.CHAR(128), nullable=False)
    role = db.Column(db.Integer, db.ForeignKey('role.id'))

    def __str__(self):
        return "GroupRoleBinding: {} -> {}".format(self.group_id, self.role.name)

    @classmethod
    def get_roles_by_id(cls, group_id):
        l = cls.query.filter_by(group_id=group_id)
        return [binding.role for binding in l]