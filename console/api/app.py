# -*- coding: utf-8 -*-

from flask import abort, request, g
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError
from webargs.flaskparser import use_args

from console.libs.validation import RegisterSchema, UserSchema
from console.libs.view import create_api_blueprint, DEFAULT_RETURN_VALUE, user_require
from console.models.app import App, Release
from console.models.user import User


bp = create_api_blueprint('app', __name__, 'app')


def _get_app(appname):
    app = App.get_by_name(appname)
    if not app:
        abort(404, 'App not found: {}'.format(appname))

    if not g.user.granted_to_app(app):
        abort(403, 'You\'re not granted to this app, ask administrators for permission')

    return app


def _get_release(appname, sha):
    release = Release.get_by_app_and_sha(appname, sha)
    if not release:
        abort(404, 'Release `%s, %s` not found' % (appname, sha))

    if not g.user.granted_to_app(release.app):
        abort(403, 'You\'re not granted to this app, ask administrators for permission')

    return release


@bp.route('/')
@user_require(False)
def list_app():
    """List all the apps associated with the current logged in user, for
    administrators, list all apps

    **Example response**:

    .. sourcecode:: http

        HTTP/1.1 200 OK
        Content-Type: application/json

        [
            {
                "id": 10001,
                "created": "2018-03-21 14:54:06",
                "updated": "2018-03-21 14:54:07",
                "name": "test-app",
                "git": "git@github.com:projecteru2/console.git",
                "tackle_rule": {},
                "env_sets": {"prodenv": {"foo": "some-env-content"}}
            }
        ]
    """
    return g.user.list_app()


@bp.route('/<appname>')
@user_require(False)
def get_app(appname):
    """Get a single app

    **Example response**:

    .. sourcecode:: http

        HTTP/1.1 200 OK
        Content-Type: application/json

        {
            "id": 10001,
            "created": "2018-03-21 14:54:06",
            "updated": "2018-03-21 14:54:07",
            "name": "test-app",
            "git": "git@github.com:projecteru2/console.git",
            "tackle_rule": {},
            "env_sets": {"prodenv": {"foo": "some-env-content"}}
        }
    """
    return _get_app(appname)


@bp.route('/<appname>/users')
@user_require(False)
def get_app_users(appname):
    """List users who has permissions to the specified app

    .. todo::

        * write tests for this API
        * add example response
    """
    app = _get_app(appname)
    return app.list_users()


@bp.route('/<appname>/users', methods=['PUT'])
@use_args(UserSchema())
@user_require(False)
def grant_user(args, appname):
    """Grant permission to a user

    :<json string username: you know what this is
    :<json int user_id: must provide either username or user_id
    """
    app = _get_app(appname)
    if args['username']:
        user = User.get_by_name(args['username'])
    else:
        user = User.get(args['user_id'])

    try:
        app.grant_user(user)
    except IntegrityError as e:
        pass

    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/users', methods=['DELETE'])
@use_args(UserSchema())
@user_require(False)
def revoke_user(args, appname):
    """Revoke someone's permission to a app

    :<json string username: you know what this is
    :<json int user_id: must provide either username or user_id
    """
    app = _get_app(appname)
    if args['username']:
        user = User.get_by_name(args['username'])
    else:
        user = User.get(args['user_id'])

    return app.revoke_user(user)


@bp.route('/<appname>/pods')
@user_require(False)
def get_app_pods(appname):
    """Get all containers of the specified app

    .. todo::

        * add example response
        * test this API
    """
    app = _get_app(appname)
    # TODO

@bp.route('/<appname>/pod/<podname>')
@user_require(False)
def get_app_pod(appname, podname):
    """Get all containers of the specified app

    .. todo::

        * add example response
        * test this API
    """
    app = _get_app(appname)
    # TODO


@bp.route('/<appname>/releases')
@user_require(False)
def get_app_releases(appname):
    """List every release of the specified app

    .. todo::

        * add example response
        * test this API
    """
    app = _get_app(appname)
    return Release.get_by_app(app.name)


@bp.route('/<appname>/deployments')
@user_require(False)
def get_app_deployments(appname):
    """Get all the deployments for the specified app

    .. todo::

        * write tests for this API
        * add example response
    """
    app = _get_app(appname)
    return app.get_deployments()


@bp.route('/<appname>/version/<sha>')
@user_require(False)
def get_release(appname, sha):
    """Get one release of the specified app

    .. todo::

        * add example response
        * test this API
    """
    return _get_release(appname, sha)


@bp.route('/register', methods=['POST'])
@use_args(RegisterSchema())
@user_require(False)
def register_release(args):
    """Register a release of the specified app

    :<json string appname: required
    :<json string sha: required, must be length 40
    :<json string git: required, the repo address using git protocol, e.g. :code:`git@github.com:projecteru2/console.git`
    :<json string specs_text: required, the yaml specs for this app
    :<json string branch: optional git branch
    :<json string git_tag: optional git tag
    :<json string commit_message: optional commit message
    :<json string author: optional author
    """
    appname = args['appname']
    git = args['git']
    sha = args['sha']
    specs_text = args['specs_text']
    branch = args.get('branch')
    git_tag = args.get('git_tag')
    commit_message = args.get('commit_message')
    author = args.get('author')

    app = App.get_or_create(appname, git)
    if not app:
        abort(400, 'Error during create an app (%s, %s, %s)' % (appname, git, sha))

    try:
        release = Release.create(app, sha, specs_text, branch=branch, git_tag=git_tag,
                                 author=author, commit_message=commit_message)
    except (IntegrityError, ValidationError) as e:
        abort(400, str(e))

    if release.raw:
        release.update_image(release.specs.base)

    return release
