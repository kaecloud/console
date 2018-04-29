# -*- coding: utf-8 -*-

from loginpass import GitHub, register_to

from authlib.flask.client import OAuth, RemoteApp
from flask import session
from flask_caching import Cache
from flask_mako import MakoTemplates
from flask_session import Session
from flask_sockets import Sockets
from flask_sqlalchemy import SQLAlchemy
from redis import Redis

from console.config import REDIS_URL, OAUTH_APP_NAME


db = SQLAlchemy()
mako = MakoTemplates()
sockets = Sockets()
rds = Redis.from_url(REDIS_URL)


def fetch_token(name):
    token_session_key = '{}-token'.format(name.lower())
    return session.get(token_session_key, {})


def update_token(name, token):
    token_session_key = '{}-token'.format(name.lower())
    session[token_session_key] = token
    return token


def delete_token(name=OAUTH_APP_NAME):
    token_session_key = '{}-token'.format(name.lower())
    session.pop(token_session_key)


oauth = None
oauth_client = None


def init_oauth(app):
    global oauth, oauth_client
    oauth = OAuth(app=app, fetch_token=fetch_token, update_token=update_token)
    oauth_client = register_to(GitHub, oauth, RemoteApp)


cache = Cache(config={'CACHE_TYPE': 'redis'})
sess = Session()
