# -*- coding: utf-8 -*-

import json
from addict import Dict
from flask import abort, session, request, g

from console.config import (
    EMAIL_DOMAIN, KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_PASSWD
)
from console.ext import oidc
from console.libs.utils import logger
from console.libs.sso import SSO


def get_current_user(require_token=False, scopes_required=None):
    user = None
    # logger.debug(f"request headers: {request.headers}")
    auth = request.authorization
    # token authentication
    token = None
    if 'Authorization' in request.headers and request.headers['Authorization'].startswith('Bearer '):
        token = request.headers['Authorization'].split(None, 1)[1].strip()
    if 'access_token' in request.form:
        token = request.form['access_token']
    elif 'access_token' in request.args:
        token = request.args['access_token']

    if token is not None:
        validity = oidc.validate_token(token, scopes_required)
        logger.debug(f"validity: {validity}, token: {token}")
        if (validity is True) or (not require_token):
            user = User(g.oidc_token_info)
    elif auth is not None:
        # Basic authentication
        # we use keycloak admin user and password to do authentication,
        # and pass a real user through form, argument, or http header
        # this is mainly used in feishu bot, because sso's admin account can't get token of individual user
        username, password = auth.username, auth.password
        if username != KEYCLOAK_ADMIN_USER or password != KEYCLOAK_ADMIN_PASSWD:
            logger.debug("invalid user/password")
            return None

        if 'real_user' in request.form:
            real_user = request.form['real_user']
        elif 'real_user' in request.args:
            real_user = request.args['real_user']
        else:
            real_user = request.headers.get("X-Real-User")
        if real_user is not None:
            user = User.get_by_username(real_user)
    else:
        # try to get current user from session
        username = session.get('current_username', None)
        logger.debug(f"user from session: {username}")
        if username is not None:
            user = User.get_by_username(username)
            if not user:
                session.pop('current_username')
    if user:
        session['current_username'] = user.username
    return user


class Group(Dict):
    @classmethod
    def get_all(cls):
        group_dict_list = SSO.instance().get_groups()
        return [Group(d) for d in group_dict_list]

    @classmethod
    def get_by_name(cls, name):
        group_dict = SSO.instance().get_group_by_name(name)
        return Group(group_dict)

    def __str__(self):
        return '{class_} {u.id} {u.name}'.format(
            class_=self.__class__,
            u=self,
        )


class User(Dict):
    def __init__(self, d):
        super(User, self).__init__(d)

    @classmethod
    def get_all(cls):
        return [cls(d) for d in SSO.instance().get_users()]

    def get_groups(self):
        return [Group(grp) for grp in SSO.instance().get_groups_by_user(self['username'])]

    def __str__(self):
        return '{class_} {u.username} {u.email}'.format(
            class_=self.__class__,
            u=self,
        )

    @classmethod
    def get_by_username(cls, username):
        d = SSO.instance().get_user(username)
        if d is not None:
            d = User(d)
        return d

    def list_app(self, start=0, limit=500):
        from console.models.rbac import RBACAction, get_roles_by_user
        from console.models.app import App
        roles = get_roles_by_user(self)
        apps = []
        logger.debug(f"role list(user: {self.username}) {roles}")
        for role in roles:
            if RBACAction.KAE_ADMIN in role.action_list:
                apps = App.get_all()
                break
            if RBACAction.ADMIN in role.action_list:
                return role.app_list[start: start+limit]
            if RBACAction.GET in role.action_list:
                apps += role.app_list
        return apps[start: start+limit]

    @property
    def nickname(self):
        if 'firstName' in self and 'lastName' in self:
            return f'{self.firstName} {self.lastName}'
        return self.username
