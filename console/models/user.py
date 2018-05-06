# -*- coding: utf-8 -*-

import json
from authlib.client.errors import OAuthException
from flask import abort, session, request
from sqlalchemy.exc import IntegrityError

from console.config import OAUTH_APP_NAME, EMAIL_DOMAIN
from console.ext import db, fetch_token, update_token, oauth_client, private_token_client
from console.models.base import BaseModelMixin


def get_current_user():
    user_id = session.get('user_id', None)
    if user_id is not None:
        user = User.get_by_id(user_id)
        if not user:
            session.pop('user_id')
        else:
            return user
    token = fetch_token(OAUTH_APP_NAME)
    # check if http headers contain access token
    if not token:
        raw_token = request.headers.get('X-Access-Token', None)
        if not raw_token:
            return None
        try:
            authlib_user = private_token_client.profile(raw_token)
        except Exception as e:
            return abort(500, 'fetch {} profile failed: {}'.format(OAUTH_APP_NAME, e))
        user = User.set_authlib_user(authlib_user)
    else:
        try:
            # better for other oauth provider
            authlib_user = oauth_client.profile()
            if EMAIL_DOMAIN and (not authlib_user.email.endswith('@' + EMAIL_DOMAIN)):
                return abort(400, "invalid email {}".format(authlib_user.email))

            user = User.set_authlib_user(authlib_user)
        except OAuthException as e:
            return abort(400, 'oauth exception: {}, your session has been reset'.format(e))
        except Exception as e:
            return abort(500, 'fetch {} profile failed: {}'.format(OAUTH_APP_NAME, e))

    session['user_id'] = user.id
    return user


class User(BaseModelMixin):
    username = db.Column(db.CHAR(50), nullable=False, unique=True)
    email = db.Column(db.String(100), nullable=False, unique=True, index=True)
    nickname = db.Column(db.CHAR(50), nullable=False)
    avatar = db.Column(db.String(2000), nullable=False)
    privileged = db.Column(db.Integer, default=0)
    data = db.Column(db.Text)

    @classmethod
    def create(cls, username=None, email=None, nickname=None, avatar=None,
               privileged=0, data=None):
        if isinstance(data, dict):
            data = json.dumps(data)
        user = cls(username=username, email=email, nickname=nickname,
                   avatar=avatar, data=data)
        try:
            db.session.add(user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise
        return user

    def __str__(self):
        return '{class_} {u.name} {u.email}'.format(
            class_=self.__class__,
            u=self,
        )

    @classmethod
    def get_by_username(cls, username):
        return cls.query.filter_by(username=username).first()

    @classmethod
    def get_by_email(cls, email):
        return cls.query.filter_by(email=email).first()

    @classmethod
    def get_by_id(cls, user_id):
        return cls.query.filter_by(id=user_id).first()

    @classmethod
    def set_authlib_user(cls, auth_user):
        user = cls.query.filter_by(email=auth_user.email).first()
        username = auth_user.preferred_username
        nickname = auth_user.nickname
        if username is None:
            username = auth_user.name
        if nickname is None:
            nickname = auth_user.name

        avatar = auth_user.picture
        data = json.dumps(dict(auth_user))
        if not user:
            user = cls.create(username=username, email=auth_user.email, nickname=nickname, avatar=avatar, data=data)
        else:
            user.update(username=username, email=auth_user.email, nickname=nickname, avatar=avatar,
                        data=data)

        return user

    def granted_to_app(self, app):
        if self.privileged:
            return True
        from console.models.app import AppUserRelation
        r = AppUserRelation.query.filter_by(appname=app.name, user_id=self.id).all()
        return bool(r)

    def list_app(self):
        from console.models.app import AppUserRelation, App
        if self.privileged:
            return App.get_all()
        rs = AppUserRelation.query.filter_by(user_id=self.id)
        return [App.get_by_name(r.appname) for r in rs]

    def elevate_privilege(self):
        self.privileged = 1
        db.session.add(self)
        db.session.commit()
