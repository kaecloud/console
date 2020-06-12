# -*- coding: utf-8 -*-

from flask import jsonify, abort, g

from console.libs.view import user_require, create_api_blueprint
from console.models import (
    User, Group, check_rbac, RBACAction,
)


bp = create_api_blueprint('user', __name__, 'user')


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
    if not check_rbac([RBACAction.KAE_ADMIN], None):
        abort(403, 'Forbidden by RBAC rules, please check if you have permission.')

    return jsonify(User.get_all())


@bp.route('/groups')
@user_require(True)
def list_groups():
    """
    List all users
    """
    if not check_rbac([RBACAction.KAE_ADMIN], None):
        abort(403, 'Forbidden by RBAC rules, please check if you have permission.')
    return jsonify(Group.get_all())


@bp.route('/me')
@user_require(False)
def me():
    """
    get information of current user
    ---
    responses:
      200:
        description: user object
        schema:
          $ref: '#/definitions/User'
        examples:
          application/json: {
              "username": "haha",
              "nickname": "dude",
              "email": "name@example.com",
              "avatar": "xxx.png",
              "privileged": True,
              "data": "ggg"
            }
   """
    return g.user