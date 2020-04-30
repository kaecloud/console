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
    RESTART_CONTAINER = "restart_container"
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
    RBACAction.RESTART_CONTAINER,
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
    RBACAction.RESTART_CONTAINER,
    RBACAction.ENTER_CONTAINER,
]

role_app_association = db.Table('role_app_association',
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'), primary_key=True),
    db.Column('app_id', db.Integer, db.ForeignKey('app.id'), primary_key=True),
)


def check_rbac(app, actions, cluster=None):
    """
    check if a user has the permission, cluster is optional argument,
    if cluster set to None, then this function will not check cluster
    :param app:
    :param actions:
    :param cluster:
    :return:
    """
    username = g.user["username"]
    roles = get_roles_by_user(username)
    for role in roles:
        # kae admin can do anything
        if RBACAction.KAE_ADMIN in role.actions:
            return True

        if role.app_list and app:
            if app.name not in role.app_names:
                continue

        if cluster and role.cluster_list and (cluster not in role.cluster_list):
            continue

        if len(set(actions) - set(role.get_actions)) == 0:
            return True
    return False


def prepare_roles_for_new_app(app, user):
    """
    auto generate RBAC roles when create app, we will do following thins
    1. create a group roles(reader, writer, admin) for the group which creator belongs to
    2. add app to the groups roles created in step 1
    3. create a user role for the creator
    :param app:
    :param group_id:
    :param username:
    :return:
    """
    app_reader_name, app_writer_name, app_admin_name = f"app-{app.name}-reader", f"app-{app.name}-writer", f"app-{app.name}-admin"
    app_reader = Role.create(app_reader_name, [app], [RBACAction.GET])
    app_writer = Role.create(app_writer_name, [app], _writer_action_list)
    app_admin = Role.create(app_admin_name, [app], [RBACAction.ADMIN])
    UserRoleBinding.create(user.username, app_admin)
    db.session.commit()


def str2action(ss):
    return getattr(RBACAction, ss.upper())


def get_roles_by_user(username):
    groups = SSO.instance().get_groups_by_user(username)

    if not groups:
        return []
    roles = UserRoleBinding.get_roles_by_name(username)
    for group in groups:
        roles += GroupRoleBinding.get_roles_by_id(group.id)


class Role(BaseModelMixin):
    __tablename__ = "role"
    name = db.Column(db.CHAR(64), nullable=False, unique=True)
    apps = db.relationship('App', secondary=role_app_association,
                           backref=db.backref('roles', lazy='dynamic'), lazy='dynamic')

    # actions is a json with the following format:
    #   ["get", "deploy", "get_config"],
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

    @classmethod
    def get_by_name(cls, name):
        return cls.query.filter_by(name=name).first()

    @property
    def app_names(self):
        return [app.name for app in self.apps]

    @property
    def action_list(self):
        act_txt_list = json.loads(self.actions)
        actions = []
        if len(act_txt_list) == 0:
            actions = _all_action_list
        else:
            for action_txt in act_txt_list:
                act = getattr(RBACAction, action_txt.upper())
                actions.append(act)
        return actions

    @property
    def cluster_list(self):
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
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'))

    @classmethod
    def create(cls, username, role):
        ur = cls(username=username, role=role)
        db.session.add(ur)
        db.session.commit()

    def __str__(self):
        return "UserRoleBinding: {} -> {}".format(self.username, self.role.name)

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
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'))

    def __str__(self):
        return "GroupRoleBinding: {} -> {}".format(self.group_id, self.role.name)

    @classmethod
    def create(cls, group_id, role):
        ur = cls(group_id=group_id, role=role)
        db.session.add(ur)
        db.session.commit()

    @classmethod
    def get_roles_by_id(cls, group_id):
        l = cls.query.filter_by(group_id=group_id)
        return [binding.role for binding in l]
