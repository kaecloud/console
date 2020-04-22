# -*- coding: utf-8 -*-
import os
import json
from flask import Blueprint, jsonify, url_for, redirect, g, current_app, abort, request, session
from flask_mako import render_template
from functools import partial, wraps

from console.libs.exceptions import URLPrefixError
from console.libs.jsonutils import jsonize
from console.config import FAKE_USER
from console.models.user import User, get_current_user


ERROR_CODES = [400, 401, 403, 404, 408]
DEFAULT_RETURN_VALUE = {'error': None}


def create_ajax_blueprint(name, import_name, url_prefix=None):
    bp = Blueprint(name, import_name, url_prefix=url_prefix)

    def _error_hanlder(error):
        return jsonify({'error': error.description}), error.code

    for code in ERROR_CODES:
        bp.errorhandler(code)(_error_hanlder)

    patch_blueprint_route(bp)
    return bp


def patch_blueprint_route(bp):
    origin_route = bp.route

    def patched_route(self, rule, **options):
        def decorator(f):
            origin_route(rule, **options)(jsonize(f))
        return decorator

    bp.route = partial(patched_route, bp)


def create_page_blueprint(name, import_name, url_prefix=None):
    bp = Blueprint(name, import_name, url_prefix=url_prefix)

    def _error_hanlder(error):
        return render_template('/error/%s.mako' % error.code, err=error)

    for code in ERROR_CODES:
        bp.errorhandler(code)(_error_hanlder)

    return bp


def create_api_blueprint(name, import_name, url_prefix=None, jsonize=True, handle_http_error=True, version='v1'):
    """
    幺蛾子, 就是因为flask写API挂路由太累了, 搞了这么个东西.
    会把url_prefix挂到/api/下.
    比如url_prefix是test, 那么route全部在/api/test下
    """
    if url_prefix and url_prefix.startswith('/'):
        raise URLPrefixError('url_prefix ("%s") must not start with /' % url_prefix)

    bp_url_prefix = '/api/{}'.format(version)
    if url_prefix:
        bp_url_prefix = os.path.join(bp_url_prefix, url_prefix)
    bp = Blueprint(name, import_name, url_prefix=bp_url_prefix)

    if handle_http_error:

        def _error_hanlder(error):
            return jsonify({'error': error.description}), error.code

        for code in ERROR_CODES:
            bp.errorhandler(code)(_error_hanlder)

    # 如果不需要自动帮忙jsonize, 就不要
    # 可能的场景比如返回一个stream
    if jsonize:
        patch_blueprint_route(bp)

    return bp


def user_require(require_token=False, scopes_required=None):
    def _user_require(func):
        @wraps(func)
        def _(*args, **kwargs):
            if current_app.config['DEBUG']:
                g.user = User(FAKE_USER)
            else:
                g.user = get_current_user(require_token, scopes_required)

            if not g.user:
                # TODO: change the message
                abort(403, "please provide valid token or user/password")
            return func(*args, **kwargs)
        return _
    return _user_require
