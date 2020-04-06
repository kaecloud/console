# -*- coding: utf-8 -*-

from flask import url_for, jsonify, session, request, redirect, abort, g
from authlib.client.errors import OAuthError

from console.ext import oauth_client, update_token, delete_token
from console.libs.view import DEFAULT_RETURN_VALUE, user_require, create_ajax_blueprint
from console.models.user import User, get_current_user


bp = create_ajax_blueprint('user', __name__, url_prefix='/user')


@bp.route('/')
@user_require(True)
def list_users():
    """
    List all users
    ---
    responses:
      200:
        description: user list
        schema:
          type: array
          items:
            $ref: '#/definitions/User'
        examples:
          application/json: [
            {
              "username": "haha",
              "nickname": "dude",
              "email": "name@example.com",
              "avatar": "xxx.png",
              "privileged": True,
              "data": "ggg"
            }
          ]
    """
    return jsonify([u.to_dict() for u in User.get_all()])


@bp.route('/authorized')
def authorized():
    try:
        token = oauth_client.authorize_access_token()
    except OAuthError as e:
        abort(400, "invalid token, please try again")
    update_token(OAUTH_APP_NAME, token)
    next_url = session.pop('next', None)
    if next_url:
        return redirect(next_url)
    return redirect(url_for('user.login'))

