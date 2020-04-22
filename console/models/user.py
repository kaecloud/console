# -*- coding: utf-8 -*-

import json
from addict import Dict
from flask import abort, session, request, g
from sqlalchemy.exc import IntegrityError

from console.config import (
    EMAIL_DOMAIN, KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_PASSWD
)
from console.ext import sso, oidc
from console.models.base import BaseModelMixin
from console.libs.utils import logger


def get_current_user(require_token=False, scopes_required=None):
    username = session.get('current_username', None)
    if username is not None:
        user = User.get_by_username(username)
        if not user:
            session.pop('current_username')
        else:
            return user
        # Basic authentication
        # we use keycloak admin user and password to do authentication, 
        # and pass a real user through form, argument, or http header
        # this is mainly used in feishu bot, because sso's admin account can't get token of individual user
        auth = request.authorization
        if auth is not None:
            username, password = auth.username, auth.password
            if username != KEYCLOAK_ADMIN_USER or password != KEYCLOAK_ADMIN_PASSWD:
                return None
            real_user = None
            if 'real_user' in request.form:
                token = request.form['real_user']
            elif 'real_user' in request.args:
                real_user = request.args['real_user']
            else:
                real_user = request.headers.get("X-REAL-USER")
            if real_user is None:
                return None 
            return sso.get_user(real_user)

        # token authentication
        token = None
        if 'Authorization' in request.headers and request.headers['Authorization'].startswith('Bearer '):
            token = request.headers['Authorization'].split(None,1)[1].strip()
        if 'access_token' in request.form:
            token = request.form['access_token']
        elif 'access_token' in request.args:
            token = request.args['access_token']

        validity = oidc.validate_token(token, scopes_required)
        if (validity is True) or (not require_token):
            user = User(g.oidc_token_info)
        else:
            return None
    session['current_username'] = user.username
    return user


class Group(Dict):
    @classmethod
    def get_all(cls):
        pass 

    def __str__(self):
        return '{class_} {u.id} {u.name}'.format(
            class_=self.__class__,
            u=self,
        )

class User(Dict):
    def __init__(self, d):
        super(User, self).__init__(d)

    def get_group(self):
        gid = self['group_id']
        grp = sso.get_group(gid)
        if grp is not None:
            grp = Group(grp)
        return grp

    def __str__(self):
        return '{class_} {u.username} {u.email}'.format(
            class_=self.__class__,
            u=self,
        )

    @classmethod
    def get_by_username(cls, username):
        d = sso.get_user(username)
        if d is not None:
            d = User(d)
        return d

    def list_app(self, start=0, limit=500):
        from console.models.rbac import UserRoleBinding, GroupRoleBinding, RBACAction
        user_roles = UserRoleBinding.get_roles_by_name(self.username)
        group_roles = GroupRoleBinding.get_roles_by_id(self.group_id)
        apps = []
        for role in user_roles + group_roles:
            if RBACAction.ADMIN in role.action_list:
                return role.app_list[start: start+limit]
            if RBACAction.GET in role.action_list:
                apps += role.app_list
        return apps[start: start+limit]
            