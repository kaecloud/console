# -*- coding: utf-8 -*-
import json
import urllib.request
from urllib.parse import urljoin

from flask import session
from flask_caching import Cache
from flask_mako import MakoTemplates
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_sockets import Sockets
from flask_oidc import OpenIDConnect

from redis import StrictRedis

from console.config import (
    REDIS_URL, SSO_HOST, KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_PASSWD
)
from console.libs.sso import SSO


db = SQLAlchemy()
sockets = Sockets()
mako = MakoTemplates()
rds = StrictRedis.from_url(REDIS_URL)


def fetch_token(name):
    token_session_key = '{}-token'.format(name.lower())
    return session.get(token_session_key, {})


def update_token(name, token):
    token_session_key = '{}-token'.format(name.lower())
    session[token_session_key] = token
    return token


def delete_token(name="sso"):
    token_session_key = '{}-token'.format(name.lower())
    session.pop(token_session_key, None)


cache = Cache(config={'CACHE_TYPE': 'redis'})
sess = Session()
sso = SSO(SSO_HOST, KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_PASSWD)
oidc = OpenIDConnect()