# -*- coding: utf-8 -*-

import json
import yaml
import time

from addict import Dict
from flask import abort, g, Response, stream_with_context
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError
from webargs.flaskparser import use_args

from console.libs.validation import (
    RegisterSchema, UserSchema, RollbackSchema, SecretSchema, ConfigMapSchema,
    ScaleSchema, BuildArgsSchema, DeploySchema,
)
from console.libs.view import create_api_blueprint, DEFAULT_RETURN_VALUE, user_require
from console.models import App, Release, User, OPLog, OPType
from console.models.specs import load_specs
from console.libs.k8s import kube_api
from console.libs.k8s import ApiException
from console.config import DEFAULT_REGISTRY
from .util import build_image, BuildError, make_msg, make_errmsg

bp = create_api_blueprint('app', __name__, 'app')


def specs_contains_secrets(specs):
    for c in specs.service.containers:
        if c.secrets:
            return True
    return False


def specs_contains_configmap(specs):
    for c in specs.service.containers:
        if c.configDir:
            return True
    return False


def _update_specs(specs, cpus, memories, replicas):
    if replicas:
        specs.service.replicas = replicas

    if cpus:
        for idx, cpu_dict in cpus.items():
            if idx == '*':
                for container in specs.service.containers:
                    container.cpu = cpu_dict
            else:
                specs.service.containers[idx].cpu = cpu_dict

    if memories:
        for idx, memory_dict in memories.items():
            if idx == '*':
                for container in specs.service.containers:
                    container.memory = memory_dict
            else:
                specs.service.containers[idx].memory = memory_dict
    return Dict(specs)


def get_app_raw(appname):
    app = App.get_by_name(appname)
    if not app:
        abort(404, 'App not found: {}'.format(appname))

    if not g.user.granted_to_app(app):
        abort(403, 'You\'re not granted to this app, ask administrators for permission')

    return app


def _get_release(appname, git_tag):
    release = Release.get_by_app_and_tag(appname, git_tag)
    if not release:
        abort(404, 'Release `%s, %s` not found' % (appname, git_tag))

    if not g.user.granted_to_app(release.app):
        abort(403, 'You\'re not granted to this app, ask administrators for permission')

    return release


@bp.route('/')
@user_require(False)
def list_app():
    """
    List all the apps associated with the current logged in user, for
    administrators, list all apps
    ---
    responses:
      200:
        description: A list of app owned by current user
        schema:
          type: array
          items:
            $ref: '#/definitions/App'
        examples:
          application/json:
          - id: 10001
            created: "2018-03-21 14:54:06"
            updated: "2018-03-21 14:54:07"
            name: "test-app"
            type: "web"
            git: "git@github.com:projecteru2/console.git"
    """
    return g.user.list_app()


@bp.route('/<appname>')
@user_require(False)
def get_app(appname):
    """
    Get a single app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
    responses:
      200:
        description: Single app identified by `appname`
        schema:
          $ref: '#/definitions/App'
        examples:
          application/json: {
              "id": 10001,
              "created": "2018-03-21 14:54:06",
              "updated": "2018-03-21 14:54:07",
              "name": "test-app",
              "type": "web",
              "git": "git@github.com:projecteru2/console.git",
          }
    """
    return get_app_raw(appname)


@bp.route('/<appname>/rollback', methods=['PUT'])
@use_args(RollbackSchema())
@user_require(False)
def rollback_app(args, appname):
    """
    rollback specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
    responses:
      200:
        description: error message
        schema:
          $ref: '#/definitions/Error'
        examples:
          application/json:
            error: null
    """
    revision = args['revision']
    app = get_app_raw(appname)
    try:
        kube_api.rollback_app(appname, revision)
    except ApiException as e:
        abort(e.status, "Error when rollback kubernetes object: {}".format(str(e)))
    except ValueError as e:
        # FIXME: workaround for bug in kubernetes python client
        pass
    except Exception as e:
        abort(500, "Error when rollback kubernetes object: {}".format(str(e)))

    OPLog.create(
        user_id=g.user.id,
        appname=appname,
        tag=app.latest_release.tag,
        action=OPType.ROLLBACK_APP,
        content='rollback ap `hello`(revision {})'.format(revision),
    )
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/renew', methods=['PUT'])
@user_require(False)
def renew_app(appname):
    """
    Force kubernetes to recreate the pods of specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
    responses:
      200:
        description: error message
        schema:
          $ref: '#/definitions/Error'
        examples:
          application/json:
            error: null
    """
    app = get_app_raw(appname)
    try:
        kube_api.renew_app(appname)
    except ApiException as e:
        abort(e.status, "Error when renew kubernetes object {}".format(str(e)))
    except Exception as e:
        abort(500, "Error when renew app {}".format(str(e)))

    OPLog.create(
        user_id=g.user.id,
        appname=appname,
        tag=app.latest_release.tag,
        action=OPType.RENEW_APP,
    )
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>', methods=['DELETE'])
@user_require(False)
def delete_app(appname):
    """
    Delete a single app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
    responses:
      200:
        description: error message
        schema:
          $ref: '#/definitions/Error'
        examples:
          application/json:
            error: null
    """
    app = get_app_raw(appname)
    tag = app.latest_release.tag if app.latest_release else ""
    try:
        kube_api.delete_app(appname, app.type, ignore_404=True)
    except ApiException as e:
        abort(e.status, "Error when delete kubernetes object {}".format(str(e)))
    except Exception as e:
        abort(500, "Error when delete kubernetes object {}".format(str(e)))
    app.delete()

    OPLog.create(
        user_id=g.user.id,
        appname=appname,
        tag=tag,
        action=OPType.DELETE_APP,
    )
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/users')
@user_require(False)
def get_app_users(appname):
    """
    List users who has permissions to the specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
    responses:
      200:
        description: user list of this app
        schema:
          type: array
          items:
            $ref: '#/definitions/User'
        examples:
          application/json: [
            {
              "username": "haha",
              "nickname": "dude",
              "username": "name@example.com",
              "avatar": "xxx.png",
              "privileged": True,
              "data": "ggg"
            }
          ]
    """
    app = get_app_raw(appname)
    return app.list_users()


@bp.route('/<appname>/users', methods=['PUT'])
@use_args(UserSchema())
@user_require(False)
def grant_user(args, appname):
    """
    Grant permission to a user
    ---
    definitions:
      UserArgs:
        type: object
        properties:
          username:
            type: string
          email:
            type: string
    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: grant_user_args
        in: body
        required: true
        schema:
            $ref: '#/definitions/UserArgs'
    responses:
      200:
        description: error message
        schema:
          $ref: '#/definitions/Error'
        examples:
          application/json:
            error: null
    """
    app = get_app_raw(appname)
    if args['username']:
        user = User.get_by_username(args['username'])
    else:
        user = User.get_by_email(args['email'])

    try:
        app.grant_user(user)
    except IntegrityError as e:
        pass

    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/users', methods=['DELETE'])
@use_args(UserSchema())
@user_require(False)
def revoke_user(args, appname):
    """
    Revoke someone's permission to a app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: revoke_user_args
        in: body
        required: true
        schema:
            $ref: '#/definitions/UserArgs'
    responses:
      200:
        description: error message
        schema:
          $ref: '#/definitions/Error'
        examples:
          application/json:
            error: null
    """
    app = get_app_raw(appname)
    if args['username']:
        user = User.get_by_username(args['username'])
    else:
        user = User.get(args['user_id'])

    app.revoke_user(user)
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/pods')
@user_require(False)
def get_app_pods(appname):
    """
    Get all pods of the specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
    responses:
      200:
        description: PodList object
        examples:
          application/json: [
          ]
    """
    app = get_app_raw(appname)
    try:
        return kube_api.get_app_pods(appname=appname)
    except Exception as e:
        abort(500, str(e))


@bp.route('/<appname>/releases')
@user_require(False)
def get_app_releases(appname):
    """
    List every release of the specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
    responses:
      200:
        description: Release list
        schema:
          type: array
          items:
            $ref: '#/definitions/Release'
        examples:
          application/json:
          - app_id: 10019
            specs_text: xxxxz
            image: registry.cn-hangzhou.aliyuncs.com/kae/hello:v0.0.1
            id: 32
            misc: '{"commit_message": null, "git": "git@gitlab.com:yuyang0/hello-world.git"}'
            build_status: True
            updated: 2018-05-24 03:17:15
            created: 2018-05-24 10:00:25
            tag: v0.0.1
    """
    app = get_app_raw(appname)
    return Release.get_by_app(app.name)


@bp.route('/<appname>/version/<tag>')
@user_require(False)
def get_release(appname, tag):
    """
    Get one release of the specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: tag
        in: path
        type: string
        required: true
    responses:
      200:
        description: single Release object
        schema:
          $ref: '#/definitions/Release'
        examples:
          application/json:
            app_id: 10019
            specs_text: xxxxz
            image: registry.cn-hangzhou.aliyuncs.com/kae/hello:v0.0.1
            id: 32
            misc: '{"commit_message": null, "git": "git@gitlab.com:yuyang0/hello-world.git"}'
            build_status: True
            updated: 2018-05-24 03:17:15
            created: 2018-05-24 10:00:25
            tag: v0.0.1
    """
    return _get_release(appname, tag)


@bp.route('/<appname>/oplogs')
@user_require(False)
def get_app_oplogs(appname):
    """
    Get oplog list of the specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
    responses:
      200:
        description: single Release object
        schema:
          $ref: '#/definitions/OPLog'
        examples:
          application/json:
            id: 32
            appname: hello
            action: register_release
            tag: v0.0.1
            content: "xsxs"
            username: Jim
            updated: 2018-05-24 03:17:15
            created: 2018-05-24 10:00:25
    """
    app = get_app_raw(appname)
    return OPLog.get_by(appname=appname)


@bp.route('/<appname>/secret', methods=['POST'])
@use_args(SecretSchema())
@user_require(False)
def create_secret(args, appname):
    """
    Create secret for app
    ---
    definitions:
      DataArgs:
        type: object
        properties:
          data:
            type: string
    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: data_args
        in: body
        required: true
        schema:
          $ref: '#/definitions/DataArgs'
    responses:
      200:
        description: Error information
        schema:
          $ref: '#/definitions/Error'
        examples:
          application/json:
            error: null
    """
    data = args['data']
    # check if the user can access the App
    get_app_raw(appname)
    try:
        kube_api.create_or_update_secret(appname, data)
    except ApiException as e:
        abort(e.status, str(e))
    except Exception as e:
        abort(500, str(e))
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/secret')
@user_require(False)
def get_secret(appname):
    """
    get secret of specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
    responses:
      200:
        description: Secret dict
        examples:
          application/json: {
            "xxx": "vvv",
            "aaa": "bbb"
          }
    """
    # check if the user can access the App
    get_app_raw(appname)
    try:
        return kube_api.get_secret(appname)
    except ApiException as e:
        abort(e.status, str(e))
    except Exception as e:
        abort(500, str(e))


@bp.route('/<appname>/configmap', methods=['POST'])
@use_args(ConfigMapSchema())
@user_require(False)
def create_config_map(args, appname):
    """
    Create config for app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: data_args
        in: body
        required: true
        schema:
          $ref: '#/definitions/DataArgs'
    responses:
      200:
        description: Error information
        schema:
          $ref: '#/definitions/Error'
        examples:
          application/json:
            error: null
    """
    data = args['data']
    config_name = args['config_name']
    # check if the user can access the App
    get_app_raw(appname)
    try:
        kube_api.create_or_update_config_map(appname, data, config_name=config_name)
    except ApiException as e:
        abort(e.status, str(e))
    except Exception as e:
        abort(500, str(e))
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/configmap')
@user_require(False)
def get_config_map(appname):
    """
    get config of specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
    responses:
      200:
        description: Error information
        schema:
          type: string
        examples:
           plain/text:
             "aaa=11"
    """
    # check if the user can access the App
    get_app_raw(appname)
    try:
        return kube_api.get_config_map(appname)
    except ApiException as e:
        abort(e.status, str(e))
    except Exception as e:
        abort(500, str(e))


@bp.route('/register', methods=['POST'])
@use_args(RegisterSchema())
@user_require(False)
def register_release(args):
    """
    Register a release of the specified app
    ---
    definitions:
      RegisterArgs:
        type: object
        properties:
          appname:
            type: string
          tag:
            type: string
          git:
            type: string
          specs_text:
            type: string
          branch:
            type: string
          commit_message:
            type: string
          author:
            type: string

    parameters:
      - name: register_args
        in: body
        required: true
        schema:
          $ref: '#/definitions/RegisterArgs'
    responses:
      200:
        description: Release oboject
        schema:
          $ref: '#/definitions/Release'
      400:
        description: Error information
        schema:
          $ref: '#/definitions/Error'
        examples:
          error: "xxx"
    """
    appname = args['appname']
    git = args['git']
    tag = args['tag']
    specs_text = args['specs_text']
    branch = args.get('branch')
    commit_message = args.get('commit_message')
    author = args.get('author')

    # check the format of specs
    try:
        yaml_dict = yaml.load(specs_text)
    except yaml.YAMLError as e:
        return abort(400, 'specs text is invalid yaml {}'.format(str(e)))
    try:
        specs = load_specs(yaml_dict, tag)
    except ValidationError as e:
        return abort(400, 'specs text is invalid {}'.format(str(e)))
    # because some defaults may have added to specs, so we need update specs_text
    specs_text = yaml.dump(specs.to_dict())

    app = App.get_or_create(appname, git, specs.type)
    if not app:
        abort(400, 'Error during create an app (%s, %s, %s)' % (appname, git, tag))
    if app.type != specs.type:
        abort(400, "Current app type is {} and You can't change it".format(app.type))
    try:
        app.grant_user(g.user)
    except IntegrityError as e:
        pass

    default_release_image = None
    build_status = False if specs.builds else True
    for build in specs.builds:
        if build.get("name") == appname:
            default_release_image = "{}/{}:{}".format(DEFAULT_REGISTRY.rstrip('/'), appname, tag)

    try:
        release = Release.create(app, tag, specs_text, image=default_release_image,
                                 build_status=build_status,
                                 branch=branch, author=author, commit_message=commit_message)
    except IntegrityError as e:
        return abort(400, 'release is duplicate')
    except ValueError as e:
        return abort(400, str(e))

    OPLog.create(
        user_id=g.user.id,
        appname=appname,
        tag=release.tag,
        action=OPType.REGISTER_RELEASE,
    )
    return release


@bp.route('/<appname>/scale', methods=['PUT'])
@use_args(ScaleSchema())
@user_require(False)
def scale_app(args, appname):
    """
    scale specified app
    ---
    definitions:
      ScaleArgs:
        type: object
        properties:
          cpus:
            type: object
          memories:
            type: object
          replicas:
            type: string

    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: scale_args
        in: body
        required: true
        schema:
          $ref: '#/definitions/ScaleArgs'
    responses:
      200:
        description: multiple stream messages
        schema:
          $ref: '#/definitions/StreamMessage'
      400:
        description: Error information
        schema:
          $ref: '#/definitions/Error'
        examples:
          error: "xxx"
    """
    def generate():
        app = App.get_by_name(appname)
        if not app:
            yield make_errmsg('app {} not found'.format(appname))
            return

        try:
            k8s_deplopyment = kube_api.get_deployment(appname)
        except Exception as e:
            yield make_errmsg("error when get deployment {}".format(str(e)))
            return

        release_tag = k8s_deplopyment.metadata.annotations['release_tag']
        specs_text = k8s_deplopyment.metadata.annotations['app_specs_text']

        release = Release.get_by_app_and_tag(appname, release_tag)
        specs = load_specs(json.loads(specs_text), release_tag)

        # update current specs
        replicas = args.get('replicas')
        cpus = args.get('cpus')
        memories = args.get('memories')
        if not replicas:
            replicas = k8s_deplopyment.spec.replicas

        try:
            specs = _update_specs(specs, cpus, memories, replicas)
        except IndexError:
            yield make_errmsg("cpus or memories' index is larger than the number of containers")
            return

        version = k8s_deplopyment.metadata.resource_version
        if k8s_deplopyment.spec.template.metadata.annotations is None:
            renew_id = None
        else:
            renew_id = k8s_deplopyment.spec.template.metadata.annotations.get("renew_id", None)
        try:
            kube_api.update_app(appname, specs, release_tag, version=version, renew_id=renew_id)
        except Exception as e:
            yield make_errmsg('replace kubernetes deployment error: {}, {}'.format(str(e), version))
            return

        OPLog.create(
            user_id=g.user.id,
            appname=appname,
            tag=release.tag,
            action=OPType.SCALE_APP,
            content="scale app `hello`(replicas {})".format(replicas)
        )
        while True:
            time.sleep(2)
            pods = kube_api.get_app_pods(appname=appname)
            d = {
                'error': None,
                'pods': [],
            }
            ready_pods = 0
            for item in pods.items:
                name = item.metadata.name
                status = item.status.phase
                if item.status.container_statuses is None:
                    continue
                ready = sum([1 if c_status.ready else 0 for c_status in item.status.container_statuses])
                pod = {
                    'name': name,
                    'status': status,
                    'ready': ready,
                }
                d['pods'].append(pod)
                if ready == len(item.status.container_statuses):
                    ready_pods += 1
            yield (json.dumps(d) + '\n')
            if ready_pods == specs.service.replicas:
                break
    return Response(stream_with_context(generate()))


@bp.route('/<appname>/build', methods=['PUT'])
@use_args(BuildArgsSchema())
@user_require(False)
def build_app(args, appname):
    """Build an image for the specified release, the API will return all docker
    build messages, key frames as shown in the example responses
    ---
    definitions:
      BuildArgs:
        type: object
        properties:
          tag:
            type: object

    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: build_args
        in: body
        required: true
        schema:
          $ref: '#/definitions/BuildArgs'
    responses:
      200:
        description: multiple stream messages
        schema:
          $ref: '#/definitions/StreamMessage'
      400:
        description: Error information
        schema:
          $ref: '#/definitions/Error'
        examples:
          error: "xxx"
    """
    def generate():
        tag = args["tag"]

        app = App.get_by_name(appname)
        if not app:
            yield make_errmsg('app {} not found'.format(appname))
            return

        if not g.user.granted_to_app(app):
            yield make_errmsg('You\'re not granted to this app, ask administrators for permission')
            return
        release = app.get_release_by_tag(tag)
        if not release:
            yield make_errmsg('release {} not found.'.format(tag))
            return

        try:
            yield from build_image(appname, release)
        except BuildError as e:
            yield str(e)
    return Response(stream_with_context(generate()))


@bp.route('/<appname>/deploy', methods=['PUT'])
@use_args(DeploySchema())
@user_require(False)
def deploy_app(args, appname):
    """
    deployment app to kubernetes
    ---
    definitions:
      DeployArgs:
        type: object
        properties:
          tag:
            type: string
            required: true
          specs_text:
            type: string
          cpus:
            type: object
          memories:
            type: object
          replicas:
            type: integer

    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: deploy_args
        in: body
        required: true
        schema:
          $ref: '#/definitions/DeployArgs'
    responses:
      200:
        description: multiple stream messages
        schema:
          $ref: '#/definitions/StreamMessage'
      400:
        description: Error information
        schema:
          $ref: '#/definitions/Error'
        examples:
          error: "xxx"
    """
    def generate():
        tag = args["tag"]
        specs_text = args.get('specs_text', None)

        app = App.get_by_name(appname)
        if not app:
            yield make_errmsg('app {} not found'.format(appname))
            return

        if not g.user.granted_to_app(app):
            yield make_errmsg('You\'re not granted to this app, ask administrators for permission')
            return
        release = app.get_release_by_tag(tag)
        if not release:
            yield make_errmsg('release {} not found.'.format(tag))
            return
        try:
            k8s_deplopyment = kube_api.get_deployment(appname, ignore_404=True)
        except Exception as e:
            yield make_errmsg("error when get deployment {}".format(str(e)))
            return

        if specs_text:
            try:
                yaml_dict = yaml.load(release.specs_text)
            except yaml.YAMLError as e:
                yield make_errmsg('specs text is invalid yaml {}'.format(str(e)))
                return
            try:
                specs = load_specs(yaml_dict, tag)
            except ValidationError as e:
                yield make_errmsg('specs text is invalid {}'.format(str(e)))
                return
        else:
            specs = release.specs
            # update specs from release
            replicas = args.get('replicas')
            cpus = args.get('cpus')
            memories = args.get('memories')

            # sometimes user may forget fo update replicas value after a scale operation,
            # so if the spec from the release, we never scale down the deployments
            if not replicas:
                replicas = specs.service.replicas
                if k8s_deplopyment is not None and k8s_deplopyment.spec.replicas > replicas:
                    replicas = k8s_deplopyment.spec.replicas
            try:
                specs = _update_specs(specs, cpus, memories, replicas)
            except IndexError:
                yield make_errmsg("cpus or memories' index is larger than the number of containers")
                return
        if release.build_status is False:
            try:
                yield from build_image(appname, release)
            except BuildError as e:
                yield str(e)
                return

        if release.build_status is False:
            yield make_errmsg("build image error")
            return
        # check secret and configmap
        if specs_contains_secrets(specs):
            try:
                kube_api.get_secret(appname)
            except Exception:
                yield make_errmsg("can't get secret, pls ensure you've added secret for {}".format(appname))
                return
        if specs_contains_configmap(specs):
            try:
                kube_api.get_config_map(appname)
            except Exception:
                yield make_errmsg("can't get config, pls ensure you've added config for {}".format(appname))
                return

        try:
            kube_api.deploy_app(specs, release.tag)
        except Exception as e:
            yield make_errmsg('kubernetes error: {}'.format(str(e)))
            return

        OPLog.create(
            user_id=g.user.id,
            appname=appname,
            tag=release.tag,
            action=OPType.DEPLOY_APP,
        )
        yield make_msg("Finished", msg='Deploy Finished')
    return Response(stream_with_context(generate()))
