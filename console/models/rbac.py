# -*- coding: utf-8 -*-

import json
import enum
from flask import g
from sqlalchemy.exc import IntegrityError

from console.ext import db
from console.libs.utils import logger, get_cluster_names
from console.libs.sso import SSO
from console.models.base import BaseModelMixin


class RBACAction(enum.Enum):
    GET = "get"
    UPDATE = "update"
    CREATE = "create"
    DELETE = "delete"
    BUILD = "build"
    GET_CONFIG = "get_config"
    UPDATE_CONFIG = "update_config"
    GET_SECRET = "get_secret"
    UPDATE_SECRET = "update_secret"
    DEPLOY = "deploy"
    UNDEPLOY = "undeploy"
    RENEW = "renew"
    ROLLBACK = "rollback"
    STOP_CONTAINER = "stop_container"
    ENTER_CONTAINER = "enter_container"

    ADMIN = "admin"
    KAE_ADMIN = "kae_admin"


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
    RBACAction.UNDEPLOY,
    RBACAction.RENEW,
    RBACAction.ROLLBACK,
    RBACAction.STOP_CONTAINER,
    RBACAction.ENTER_CONTAINER,

    RBACAction.ADMIN,  # app admin
    RBACAction.KAE_ADMIN, # kae admin, can do anything
]

_writer_action_list = [
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
    RBACAction.UNDEPLOY,
    RBACAction.RENEW,
    RBACAction.ROLLBACK,
    RBACAction.STOP_CONTAINER,
    RBACAction.ENTER_CONTAINER,
]

role_app_association = db.Table('role_app_association',
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'), primary_key=True),
    db.Column('app_id', db.Integer, db.ForeignKey('app.id'), primary_key=True),
)


def check_rbac(actions, app=None, cluster=None):
    """
    check if a user has the permission, cluster is optional argument,

    :param actions:
    :param app: if set to None, then this function will not check app
    :param cluster: if set to None, then this function will not check cluster
    :return:
    """
    username = g.user["username"]
    roles = get_roles_by_user(username)
    if not roles:
        return False
    for role in roles:
        # kae admin can do anything
        if RBACAction.KAE_ADMIN in role.action_list:
            return True

        if role.app_list and app:
            if app.name not in role.app_names:
                continue

        if cluster and role.cluster_list and (cluster not in role.cluster_list):
            continue

        if RBACAction.ADMIN in role.action_list:
            return True
        if len(set(actions) - set(role.action_list)) == 0:
            return True
    return False


def prepare_roles_for_new_app(app, user):
    """
    auto generate RBAC roles when create app, we will do following thins
    1. create three roles(reader, writer, admin)
    2. create a user role binding for admin role and app creator
    3. create group role bindings for reader role and the groups which the app creator belongs to
    :param app:
    :param user:
    :return:
    """
    app_reader_name, app_writer_name, app_admin_name = f"app-{app.name}-reader", f"app-{app.name}-writer", f"app-{app.name}-admin"
    app_reader = Role.create(app_reader_name, [app], [RBACAction.GET])
    app_writer = Role.create(app_writer_name, [app], _writer_action_list)
    app_admin = Role.create(app_admin_name, [app], [RBACAction.ADMIN])
    UserRoleBinding.create(user.username, app_admin)
    for group in user.get_groups():
        GroupRoleBinding.create(group.id, app_reader)
    db.session.commit()


def str2action(ss):
    return getattr(RBACAction, ss.upper())


def str2actions(ss):
    actions = []
    lst = json.loads(ss)
    for txt in lst:
        actions.append(getattr(RBACAction, txt.upper()))
    return actions


def get_roles_by_user(username):
    groups = SSO.instance().get_groups_by_user(username)

    if not groups:
        return []
    roles = UserRoleBinding.get_roles_by_name(username)
    for group in groups:
        roles += GroupRoleBinding.get_roles_by_id(group["id"])
    return roles


class Role(BaseModelMixin):
    __tablename__ = "role"
    name = db.Column(db.CHAR(64), nullable=False, unique=True)
    apps = db.relationship('App', secondary=role_app_association,
                           backref=db.backref('roles', lazy='dynamic'), lazy='dynamic')

    # actions is a json with the following format:
    #   ["get", "deploy", "get_config"],
    actions = db.Column(db.Text, nullable=False)
    # clusters is a json list with the following format:
    # ["cluster1", "cluster2", "cluster3"]
    # if clusters is an empty list, it mains allows all clusters
    clusters = db.Column(db.Text)

    users = db.relationship('UserRoleBinding', backref='role', lazy='dynamic')
    groups = db.relationship('GroupRoleBinding', backref='role', lazy='dynamic')

    def __str__(self):
        return self.name

    @classmethod
    def create(cls, name, apps, actions, clusters=None):
        actions_txt, clusters_txt = None, None
        if actions:
            action_vals = [act.value for act in actions]
            actions_txt = json.dumps(action_vals)
        if clusters:
            clusters_txt = json.dumps(clusters)

        r = cls(name=name, apps=apps, actions=actions_txt, clusters=clusters_txt)
        try:
            db.session.add(r)
            db.session.commit()
        except IntegrityError:
            logger.warn('Fail to create role %s', name)
            db.session.rollback()
            raise
        return r

    @classmethod
    def get_by_name(cls, name):
        return cls.query.filter_by(name=name).first()

    @property
    def app_names(self):
        return [app.name for app in self.apps]

    @property
    def action_list(self):
        try:
            actions = str2actions(self.actions)
        except AttributeError:
            logger.error("invalid action text", self.actions)
            return []
        if len(actions) == 0:
            actions = _all_action_list
        return actions

    @property
    def cluster_list(self):
        if not self.clusters:
            return get_cluster_names()
        clusters = json.loads(self.clusters)
        if len(clusters) == 0:
            return get_cluster_names()
        else:
            return clusters

    @property
    def app_list(self):
        apps = self.apps.all() 
        if len(apps) == 0:
            from console.models.app import App
            return App.get_all()
        else:
            return apps

    def to_dict(self):
        d = {
            "name": self.name,
            "apps": self.app_names,
            "actions": json.loads(self.actions),
            "clusters": self.cluster_list,
        }


class UserRoleBinding(BaseModelMixin):
    __tablename__ = "user_role_binding"
    __table_args__ = (
        db.UniqueConstraint('username', 'role_id', name='unique_user_role'),
    )
    username = db.Column(db.CHAR(128), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=False)

    @classmethod
    def create(cls, username, role):
        ur = cls(username=username, role_id=role.id)
        db.session.add(ur)
        db.session.commit()
        return ur

    def __str__(self):
        return "UserRoleBinding: {} -> {}".format(self.username, self.role)

    @classmethod
    def get_roles_by_name(cls, username):
        l = cls.query.filter_by(username=username)
        return [binding.role for binding in l]


class GroupRoleBinding(BaseModelMixin):
    __tablename__ = "group_role_binding"
    __table_args__ = (
        db.UniqueConstraint('group_id', 'role_id', name='unique_group_role'),
    )
    group_id = db.Column(db.CHAR(128), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=False)

    def __str__(self):
        return "GroupRoleBinding: {} -> {}".format(self.group_id, self.role.name)

    @classmethod
    def create(cls, group_id, role):
        gr = cls(group_id=group_id, role_id=role.id)
        db.session.add(gr)
        db.session.commit()
        return gr

    @classmethod
    def get_roles_by_id(cls, group_id):
        l = cls.query.filter_by(group_id=group_id)
        return [binding.role for binding in l]
