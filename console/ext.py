# -*- coding: utf-8 -*-
from urllib.parse import urljoin
import requests
from loginpass import create_gitlab_backend, register_to

from authlib.flask.client import OAuth, RemoteApp
from authlib.specs.oidc import UserInfo

from flask import session
from flask_caching import Cache
from flask_mako import MakoTemplates
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from redis import Redis

from console.config import REDIS_URL, OAUTH_APP_NAME, GITLAB_HOST


db = SQLAlchemy()
mako = MakoTemplates()
rds = Redis.from_url(REDIS_URL)


class PrivateTokenClient(object):
    def __init__(self, api_base_url):
        self.api_base_url = api_base_url

    def profile(self, token):
        url = urljoin(self.api_base_url, 'user')
        headers = {
            'Private-Token': token,
        }
        r = requests.get(url, headers=headers)
        data = r.json()
        params = {
            'sub': str(data['id']),
            'name': data['name'],
            'email': data.get('email'),
            'preferred_username': data['username'],
            'profile': data['web_url'],
            'picture': data['avatar_url'],
            'website': data.get('website_url'),
        }
        return UserInfo(params)


def fetch_token(name):
    token_session_key = '{}-token'.format(name.lower())
    return session.get(token_session_key, {})


def update_token(name, token):
    token_session_key = '{}-token'.format(name.lower())
    session[token_session_key] = token
    return token


def delete_token(name=OAUTH_APP_NAME):
    token_session_key = '{}-token'.format(name.lower())
    session.pop(token_session_key, None)


oauth = None
oauth_client = None
private_token_client = None


def init_oauth(app):
    global oauth, oauth_client, private_token_client
    oauth = OAuth(app=app, fetch_token=fetch_token, update_token=update_token)
    gitlab = create_gitlab_backend('gitlab', GITLAB_HOST)
    oauth_client = register_to(gitlab, oauth, RemoteApp)
    private_token_client = PrivateTokenClient(oauth_client.api_base_url)


cache = Cache(config={'CACHE_TYPE': 'redis'})
sess = Session()
