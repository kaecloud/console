# -*- coding: utf-8 -*-

import json
import yaml
import contextlib

import redis_lock
from addict import Dict
from flask import abort, g
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError
from webargs.flaskparser import use_args

from kaelib.spec import app_specs_schema

from console.libs.validation import (
    RegisterSchema, CreateAppArgsSchema, RollbackSchema, SecretArgsSchema, ConfigMapArgsSchema,
    ScaleSchema, DeploySchema, ClusterArgSchema, OptionalClusterArgSchema, ABTestingSchema,
    ClusterCanarySchema, SpecsArgsSchema, AppYamlArgsSchema, PaginationSchema, PodLogArgsSchema,
    PodEntryArgsSchema, AppCanaryWeightArgSchema,
)

from console.libs.utils import (
    logger, make_canary_appname, im_sendmsg, make_app_redis_key,
    make_errmsg,
)
from console.libs.view import create_api_blueprint, DEFAULT_RETURN_VALUE, user_require
from console.models import (
    App, Release, DeployVersion, User, OPLog, OPType, AppYaml, AppConfig,
    RBACAction, check_rbac, prepare_roles_for_new_app, delete_roles_relate_to_app,
)
from console.libs.k8s import KubeApi, KubeError, ANNO_DEPLOY_INFO, ANNO_CONFIG_ID
from console.libs.k8s import ApiException
from console.config import (
    DEFAULT_REGISTRY, IM_WEBHOOK_CHANNEL,
    TASK_PUBSUB_CHANNEL, TASK_PUBSUB_EOF, PROTECTED_CLUSTER
)
from console.ext import rds

bp = create_api_blueprint('app', __name__, 'app')


def fix_app_spec(spec, appname, tag):
    """
    override some fields of the spec
    - appname
    - set build tag if necessary
    - set image for container if necessary
    :param spec:
    :param appname:
    :param git:
    :param tag:
    :return:
    """
    spec['appname'] = appname
    svc = spec["service"]

    registry = svc.get('registry', None)
    if registry is None:
        registry = DEFAULT_REGISTRY

    default_release_image = None
    for build in spec["builds"]:
        name = build.get("name", None)
        if name == appname:
            # overwrite the build tag to release tag
            build['tag'] = tag
            default_release_image = "{}/{}:{}".format(registry.rstrip('/'), appname, tag)

    containers = spec["service"]["containers"]
    for container in containers:
        if "image" not in container:
            if not default_release_image:
                raise ValidationError("you must set image for container")
            container["image"] = default_release_image
        container['image'] = container['image'].replace('${TAG}', tag)


@contextlib.contextmanager
def handle_k8s_error(msg="Error:"):
    try:
        yield
    except ApiException as e:
        abort(e.status, str(e))
    except Exception as e:
        logger.exception(msg)
        abort(500, "internal error, please retry and contact administrator")


@contextlib.contextmanager
def lock_app(appname):
    name = appname
    if isinstance(name, dict):
        name = name['appname']
    lock_name = "__app_lock_{}_aaa".format(name)
    with redis_lock.Lock(rds, lock_name, expire=30, auto_renewal=True):
        yield


def get_spec_secret_keys(specs):
    keys = []
    for c in specs.service.containers:
        if c.secrets:
            keys.extend(c.secrets.keyList)
    return keys


def get_spec_configmap_keys(specs):
    keys = []
    for c in specs.service.containers:
        for cfg in c.configs:
            keys.append(cfg.key)
    return keys


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


def get_app_raw(appname, actions=None, cluster=None):
    app = App.get_by_name(appname)
    if not app:
        abort(404, 'App not found: {}'.format(appname))

    if not check_rbac(actions, app, cluster):
        abort(403, 'Forbidden by RBAC rules, please check if you have permission.')

    return app


def _get_release(appname, git_tag):
    release = Release.get_by_app_and_tag(appname, git_tag)
    if not release:
        abort(404, 'Release `%s, %s` not found' % (appname, git_tag))

    if not check_rbac([RBACAction.GET, ], release.app):
        abort(403, 'You\'re not granted to this app, ask administrators for permission')

    return release


def _get_canary_info(appname, cluster):
    canary_appname = make_canary_appname(appname)
    with handle_k8s_error("Error when get app {} canary".format(appname)):
        dp = KubeApi.instance().get_deployment(canary_appname, cluster_name=cluster, ignore_404=True)
    info = {}
    if dp is None:
        info['status'] = False
    else:
        info['status'] = True
        info['spec'] = dp.metadata.annotations.get('spec')
    return info


def _validate_secret_keys(appname, specs, cluster):
    # check secret
    secret_keys = get_spec_secret_keys(specs)
    if len(secret_keys) > 0:
        try:
            secret_data = KubeApi.instance().get_secret(appname, cluster_name=cluster)
        except ApiException as e:
            if e.status == 404:
                abort(403, "please set secret for app {}".format(appname))
                return   # disable the IDE warning
            else:
                raise e
        diff_keys = set(secret_keys) - set(secret_data.keys())
        if len(diff_keys) > 0:
            abort(403, "%s are not in secret, please set it first" % str(diff_keys))


def _validate_configmap_keys(appname, specs, app_config):
    configmap_keys = get_spec_configmap_keys(specs)
    if len(configmap_keys) > 0:
        if app_config is None:
            abort(403, "please set configmap for app {} and set use_newest_config when deploy".format(appname))
        cm_data = app_config.data_dict
        diff_keys = set(configmap_keys) - set(cm_data.keys())
        if len(diff_keys) > 0:
            abort(403, "%s are not in configmap" % str(diff_keys))


@bp.route('/')
@use_args(PaginationSchema(), location="query")
@user_require(True)
def list_app(args):
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
            git: "git@github.com:kaecloud/console.git"
    """
    limit = args['size']
    start = (args['page'] - 1) * limit
    return g.user.list_app(start, limit)


@bp.route('/', methods=['POST'])
@use_args(CreateAppArgsSchema())
@user_require(True)
def create_app(args):
    """
    create a app
    ---
    parameters:
      - name: app_args
        in: body
        required: true
        schema:
          $ref: '#/definitions/App'
    responses:
      200:
        description: app created
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
            git: "git@github.com:kaecloud/console.git"
    """
    appname = args['appname']
    git = args['git']
    type = args['type']

    app = App.get_by_name(appname)
    if app is not None:
        abort(403, f"app {appname} already exists")

    # create a new app
    app = App.create(appname, git, type, [g.user])
    if not app:
        abort(400, 'Error during create an app (%s, %s, %s)' % (appname, git, type))
    try:
        prepare_roles_for_new_app(app, g.user)
    except Exception as e:
        logger.exception("failed to grant user {} to app {}".format(g.user.nickname, appname))
        # app.delete()
        abort(500, "internal server error")
    return app


@bp.route('/<appname>')
@user_require(True)
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
              "git": "git@github.com:kaecloud/console.git",
          }
    """
    return get_app_raw(appname, [RBACAction.GET, ])


@bp.route('/<appname>/rollback', methods=['PUT'])
@use_args(RollbackSchema())
@user_require(True)
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
    cluster = args['cluster']
    app = get_app_raw(appname, [RBACAction.ROLLBACK], cluster)

    with lock_app(appname):
        canary_info = _get_canary_info(appname, cluster)
        if canary_info['status']:
            abort(403, "Please delete canary release before rollback app")

        with handle_k8s_error("failed to get kubernetes deployment of app {}".format(appname)):
            k8s_deployment = KubeApi.instance().get_deployment(appname, cluster_name=cluster)

        version = k8s_deployment.metadata.resource_version
        deploy_id = k8s_deployment.metadata.annotations['deploy_id']

        if k8s_deployment.spec.template.metadata.annotations is None:
            renew_id = None
        else:
            renew_id = k8s_deployment.spec.template.metadata.annotations.get("renew_id", None)

        previous_ver = DeployVersion.get_previous_version(deploy_id, revision)
        if previous_ver is None:
            abort(403, "invalid revision")

        specs = previous_ver.specs
        # we never decrease replicas when rollback
        if k8s_deployment is not None and k8s_deployment.spec.replicas > specs.service.replicas:
            specs.service.replicas = k8s_deployment.spec.replicas

        with handle_k8s_error('Error when update app({}:{})'.format(appname, version)):
            KubeApi.instance().update_app(
                appname, specs, previous_ver,
                cluster_name=cluster, version=version, renew_id=renew_id)

    OPLog.create(
        username=g.user.username,
        app_id=app.id,
        appname=appname,
        cluster=cluster,
        tag=app.latest_release.tag,
        action=OPType.ROLLBACK_APP,
        content=f'rollback app `{appname}`(revision {revision})',
    )
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/renew', methods=['PUT'])
@use_args(ClusterArgSchema())
@user_require(True)
def renew_app(args, appname):
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
    cluster = args['cluster']
    app = get_app_raw(appname, [RBACAction.RENEW, ], cluster)

    with lock_app(appname):
        with handle_k8s_error("Error when renew app {}".format(appname)):
            KubeApi.instance().renew_app(appname, cluster_name=cluster)

    OPLog.create(
        username=g.user.username,
        app_id=app.id,
        appname=appname,
        cluster=cluster,
        tag=app.latest_release.tag,
        action=OPType.RENEW_APP,
    )
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>', methods=['DELETE'])
@user_require(True)
def delete_app(appname):
    """
    Delete a single app, need admin role
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
    app = get_app_raw(appname, [RBACAction.ADMIN, ])
    tag = app.latest_release.tag if app.latest_release else ""

    with lock_app(appname):
        with handle_k8s_error("Error when delete app {}".format(appname)):
            KubeApi.instance().undeploy_app(appname, app.type, ignore_404=True, cluster_name=KubeApi.ALL_CLUSTER)

    delete_roles_relate_to_app(app)
    app.delete()

    OPLog.create(
        username=g.user.username,
        app_id=app.id,
        appname=appname,
        tag=tag,
        action=OPType.DELETE_APP,
    )

    msg = 'Warning: App **{}** has been deleted by **{}**.'.format(appname, g.user.nickname)
    im_sendmsg(IM_WEBHOOK_CHANNEL, msg)
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/users')
@user_require(True)
def get_app_roles(appname):
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
              "email": "name@example.com",
              "privileged": True,
              "data": "ggg"
            }
          ]
    """
    app = get_app_raw(appname, [RBACAction.GET, ])
    #TODO


@bp.route('/<appname>/pod/<podname>/log')
@use_args(PodLogArgsSchema(), location="query")
@user_require(True)
def get_app_pod_log(args, appname, podname):
    """
    Get pod log
    """
    cluster = args['cluster']
    container = args.get('container', None)
    get_app_raw(appname, [RBACAction.GET], cluster)

    kwargs = {
        'cluster_name': cluster,
    }
    if container:
        kwargs['container'] = container
    with handle_k8s_error("Error when get app pods ({})".format(appname)):
        data = KubeApi.instance().get_pod_log(podname, **kwargs)
        return {'data': data}


@bp.route('/<appname>/pods')
@use_args(ClusterCanarySchema(), location="query")
@user_require(True)
def get_app_pods(args, appname):
    """
    Get all pods of the specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: cluster
        in: query
        type: string
        required: true
    responses:
      200:
        description: PodList object
        examples:
          application/json: [
          ]
    """
    cluster = args['cluster']
    canary = args["canary"]
    app = get_app_raw(appname, [RBACAction.GET], cluster)
    name = appname
    if canary:
        name = "{}-canary".format(appname)

    with handle_k8s_error("Error when get app pods ({})".format(appname)):
        return KubeApi.instance().get_app_pods(name=name, cluster_name=cluster)


@bp.route('/<appname>/deployment')
@use_args(ClusterCanarySchema(), location="query")
@user_require(True)
def get_app_k8s_deployment(args, appname):
    """
    Get kubernetes deployment object of the specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: cluster
        in: query
        type: string
        required: true
    responses:
      200:
        description: Deployment object
        examples:
          application/json: [
          ]
    """
    cluster = args['cluster']
    canary = args['canary']
    app = get_app_raw(appname, [RBACAction.GET, ], cluster)
    name = "{}-canary".format(appname) if canary else appname
    if not app:
        abort(404, "app {} not found".format(appname))

    with handle_k8s_error("Error when get kubernetes deployment object"):
        return KubeApi.instance().get_deployment(name, cluster_name=cluster)


@bp.route('/<appname>/ingress')
@use_args(ClusterArgSchema(), location="query")
@user_require(True)
def get_app_k8s_ingress(args, appname):
    """
    Get kubernetes ingress object of the specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: cluster
        in: query
        type: string
        required: true
    responses:
      200:
        description: Ingress object
        examples:
          application/json: [
          ]
    """
    cluster = args['cluster']
    app = get_app_raw(appname, [RBACAction.GET, ], cluster)
    if not app:
        abort(404, "app {} not found".format(appname))

    with handle_k8s_error("Error when get kubernetes ingress object"):
        return KubeApi.instance().get_ingress(appname, cluster_name=cluster)


@bp.route('/<appname>/releases')
@use_args(PaginationSchema(), location="query")
@user_require(True)
def get_app_releases(args, appname):
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
    app = get_app_raw(appname, [RBACAction.GET, ])
    limit = args['size']
    start = (args['page'] - 1) * limit
    return Release.get_by_app(app.name, start, limit)


@bp.route('/<appname>/version/<tag>')
@user_require(True)
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


@bp.route('/<appname>/version/<tag>/spec', methods=['POST'])
@use_args(SpecsArgsSchema())
@user_require(True)
def update_release_spec(args, appname, tag):
    """
    update release's spec
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
    release = _get_release(appname, tag)
    specs_text = args['specs_text']
    # check the format of specs
    try:
        yaml_dict = yaml.load(specs_text)
        # we can't change the builds part of the spec
        yaml_dict['builds'] = release.specs_dict['builds']
    except yaml.YAMLError as e:
        return abort(400, 'specs text is invalid yaml {}'.format(str(e)))
    try:
        specs = app_specs_schema.load(yaml_dict).data
        fix_app_spec(specs, appname, tag)
    except ValidationError as e:
        return abort(400, 'specs text is invalid {}'.format(str(e)))

    # because some defaults may have added to specs, so we need update specs_text
    specs_text = yaml.dump(specs.to_dict())

    release.specs_text = specs_text
    release.save()

    OPLog.create(
        username=g.user.username,
        app_id=release.app_id,
        appname=appname,
        tag=release.tag,
        action=OPType.UPDATE_RELEASE,
    )
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/version/<tag>/spec')
@user_require(True)
def get_release_spec(appname, tag):
    """
    get release's spec
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
    release = _get_release(appname, tag)
    return {
        "spec": release.specs_text,
    }


@bp.route('/<appname>/oplogs')
@user_require(True)
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
    app = get_app_raw(appname, [RBACAction.GET, ])
    return OPLog.get_by(app_id=app.id)


@bp.route('/<appname>/secret', methods=['POST'])
@use_args(SecretArgsSchema())
@user_require(True)
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
    cluster = args['cluster']
    data = args['data']
    replace = args['replace']
    # check if the user can access the App
    get_app_raw(appname, [RBACAction.UPDATE_SECRET, ], cluster)
    with handle_k8s_error("Failed to create secret"):
        KubeApi.instance().create_or_update_secret(appname, data, replace=replace, cluster_name=cluster)
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/secret')
@use_args(ClusterArgSchema(), location="query")
@user_require(True)
def get_secret(args, appname):
    """
    get secret of specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: cluster
        in: query
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
    cluster = args['cluster']
    # check if the user can access the App
    get_app_raw(appname, [RBACAction.GET_SECRET, ], cluster)
    with handle_k8s_error("Failed to get secret"):
        return KubeApi.instance().get_secret(appname, cluster_name=cluster)


@bp.route('/<appname>/configmap', methods=['POST'])
@use_args(ConfigMapArgsSchema())
@user_require(True)
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
    cluster = args['cluster']
    cm_data = args['data']
    replace = args['replace']
    # check if the user can access the App
    app = get_app_raw(appname, [RBACAction.UPDATE_CONFIG, ], cluster)
    if not replace:
        newest_cfg = AppConfig.get_newest_config(app, cluster)
        if newest_cfg is not None:
            exist_data = newest_cfg.data_dict
        else:
            exist_data = {}
        exist_data.update(cm_data)
        cm_data = exist_data
    AppConfig.create(app, cluster, cm_data)

    OPLog.create(
        username=g.user.username,
        appname=appname,
        app_id=app.id,
        action=OPType.UPDATE_CONFIG,
    )
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/configmap')
@use_args(ClusterArgSchema(), location="query")
@user_require(True)
def get_config_map(args, appname):
    """
    get config of specified app
    ---
    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: cluster
        in: query
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
    cluster = args['cluster']
    # check if the user can access the App
    app = get_app_raw(appname, [RBACAction.GET_CONFIG, ], cluster)
    newest_cfg = AppConfig.get_newest_config(app, cluster)

    with handle_k8s_error("Failed to get config map"):
        raw_data = KubeApi.instance().get_config_map(appname, cluster_name=cluster, ignore_404=True)
    res = {
        "newest": newest_cfg.data_dict if newest_cfg else None,
        "current": raw_data,
    }
    return res


@bp.route('/<appname>/yaml')
@user_require(True)
def list_app_yaml(appname):
    """
    Create or Update app yaml
    ---
    """
    app = get_app_raw(appname, [RBACAction.GET, ])
    return AppYaml.get_by_app(app)


@bp.route('/<appname>/yaml', methods=['POST'])
@use_args(AppYamlArgsSchema())
@user_require(True)
def create_app_yaml(args, appname):
    """
    Create or Update app yaml
    ---
    """
    name = args['name']
    specs_text = args['specs_text']
    comment = args.get('comment', '')

    # check the format of specs
    try:
        yaml_dict = yaml.load(specs_text)
    except yaml.YAMLError as e:
        return abort(400, 'specs text is invalid yaml {}'.format(str(e)))
    try:
        specs = app_specs_schema.load(yaml_dict).data
        # at this place, we just use fix_app_spec to check if the default values in spec are correct
        # we don't change the spec text, because AppYaml is independent with any release.
        fix_app_spec(specs, appname, 'v0.0.1')
    except ValidationError as e:
        return abort(400, 'specs text is invalid {}'.format(str(e)))

    # check if the user can access the App
    app = get_app_raw(appname, [RBACAction.UPDATE])
    app_yaml = AppYaml.get_by_app_and_name(app, name)
    if not app_yaml:
        AppYaml.create(name, app, specs_text, comment)
    else:
        abort(409, "app yaml already exist")
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/name/<name>/yaml', methods=['POST'])
@use_args(AppYamlArgsSchema())
@user_require(True)
def update_app_yaml(args, appname, name):
    """
    Delete app yaml
    ---
    """
    new_name = args['name']
    specs_text = args['specs_text']
    comment = args.get('comment', '')
    # check the format of specs
    try:
        yaml_dict = yaml.load(specs_text)
    except yaml.YAMLError as e:
        return abort(400, 'specs text is invalid yaml {}'.format(str(e)))
    try:
        specs = app_specs_schema.load(yaml_dict).data
        # at this place, we just use fix_app_spec to check if the default values in spec are correct
        # we don't change the spec text, because AppYaml is independent with any release.
        fix_app_spec(specs, appname, 'v0.0.1')
    except ValidationError as e:
        return abort(400, 'specs text is invalid {}'.format(str(e)))

    app = get_app_raw(appname, [RBACAction.UPDATE])
    app_yaml = AppYaml.get_by_app_and_name(app, name)
    if not app_yaml:
        abort(404, "AppYaml(app: {}, name:{}) not found".format(appname, name))
    if (not comment) and app_yaml.comment:
        comment = app_yaml.comment
    app_yaml.update(name=new_name, comment=comment, specs_text=specs_text)
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/name/<name>/yaml', methods=['DELETE'])
@user_require(True)
def delete_app_yaml(appname, name):
    """
    Delete app yaml
    ---
    """
    app = get_app_raw(appname, [RBACAction.DELETE, ])
    app_yaml = AppYaml.get_by_app_and_name(app, name)
    if not app_yaml:
        abort(404, "AppYaml(app: {}, name:{}) not found".format(appname, name))
    app_yaml.delete()
    return DEFAULT_RETURN_VALUE


@bp.route('/register', methods=['POST'])
@use_args(RegisterSchema())
@user_require(True)
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
          force:
            type: boolean

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
    force = args['force']

    # check the format of specs
    try:
        yaml_dict = yaml.load(specs_text)
    except yaml.YAMLError as e:
        return abort(400, 'specs text is invalid yaml {}'.format(str(e)))
    try:
        specs = app_specs_schema.load(yaml_dict).data
        fix_app_spec(specs, appname, tag)
    except ValidationError as e:
        return abort(400, 'specs text is invalid: {}'.format(str(e)))

    # because some defaults may have added to specs, so we need update specs_text
    new_specs_text = yaml.dump(specs.to_dict())

    app = App.get_by_name(appname)
    if not app:
        # create a new app
        app = App.create(appname, git, specs.type, [g.user])
        if not app:
            abort(400, 'Error during create an app (%s, %s, %s)' % (appname, git, tag))
        try:
            prepare_roles_for_new_app(app, g.user)
        except Exception as e:
            logger.exception("failed to grant user {} to app {}".format(g.user.nickname, appname))
            # app.delete()
            abort(500, "internal server error")
    else:
        if not check_rbac([RBACAction.UPDATE], app):
            abort(403, 'Forbidden by RBAC rules, please check if you have permission.')
        if app.type != specs.type:
            abort(400, "Current app type is {} and You can't change it to {}".format(app.type, specs.type))
    # create default AppYaml if it doesn't exist
    app_yaml = AppYaml.get_by_app_and_name(app, 'default')
    if not app_yaml:
        AppYaml.create(name='default', app=app, specs_text=specs_text, comment='create by release {}'.format(tag))

    default_release_image = None
    build_status = False if specs.builds else True
    for build in specs.builds:
        if build.get("name") == appname:
            default_release_image = "{}/{}:{}".format(DEFAULT_REGISTRY.rstrip('/'), appname, tag)

    release = Release.get_by_app_and_tag(appname, tag)
    if not release:
        try:
            release = Release.create(app, tag, new_specs_text, image=default_release_image,
                                     build_status=build_status,
                                     branch=branch, author=author, commit_message=commit_message)
        except IntegrityError as e:
            return abort(400, 'concurrent conflict, please retry')
        except ValueError as e:
            return abort(400, str(e))
    else:
        if force is True:
            release.update(new_specs_text, image=default_release_image,
                           build_status=build_status,
                           branch=branch, author=author, commit_message=commit_message)
        else:
            return abort(400, 'release is duplicate')

    OPLog.create(
        username=g.user.username,
        appname=appname,
        app_id=app.id,
        tag=release.tag,
        action=OPType.REGISTER_RELEASE,
    )
    return release


@bp.route('/<appname>/scale', methods=['PUT'])
@use_args(ScaleSchema())
@user_require(True)
def scale_app(args, appname):
    """
    scale specified app
    ---
    definitions:
      ScaleArgs:
        type: object
        properties:
          cluster:
            type: string
            required: true
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
    cluster = args['cluster']
    app = get_app_raw(appname, [RBACAction.SCALE], cluster)

    with lock_app(appname):
        with handle_k8s_error("Error when get deployment"):
            k8s_deployment = KubeApi.instance().get_deployment(appname, cluster_name=cluster)

        deploy_info = json.loads(k8s_deployment.metadata.annotations[ANNO_DEPLOY_INFO])
        version = k8s_deployment.metadata.resource_version

        try:
            cur_deploy_ver = DeployVersion.get(id=deploy_info['deploy_id'])
        except:
            logger.exception("can't get current deploy version")
            return abort(500, "internal error")

        specs = cur_deploy_ver.specs

        # update current specs
        replicas = args.get('replicas')
        cpus = args.get('cpus')
        memories = args.get('memories')
        if not replicas:
            replicas = k8s_deployment.spec.replicas

        try:
            specs = _update_specs(specs, cpus, memories, replicas)
        except IndexError:
            abort(403, "cpus or memories' index is larger than the number of containers")

        if k8s_deployment.spec.template.metadata.annotations is None:
            renew_id = None
        else:
            renew_id = k8s_deployment.spec.template.metadata.annotations.get("renew_id", None)

        with handle_k8s_error("Error when scale app {}".format(appname)):
            KubeApi.instance().update_app(appname, specs, cur_deploy_ver,
                                          version=version, renew_id=renew_id, cluster_name=cluster)
    OPLog.create(
        username=g.user.username,
        app_id=app.id,
        appname=appname,
        cluster=cluster,
        tag=cur_deploy_ver.tag,
        action=OPType.SCALE_APP,
        content=f"scale app `{appname}`(replicas {replicas})"
    )
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/deploy', methods=['PUT'])
@use_args(DeploySchema())
@user_require(True)
def deploy_app(args, appname):
    """
    deploy app to kubernetes
    ---
    definitions:
      DeployArgs:
        type: object
        properties:
          cluster:
            type: string
            required: true
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
    cluster = args['cluster']
    tag = args["tag"]
    app_yaml_name = args['app_yaml_name']
    use_newest_config = args['use_newest_config']

    app = get_app_raw(appname, [RBACAction.DEPLOY], cluster)

    if cluster in PROTECTED_CLUSTER and app.rank != 1:
        abort(403, 'This app is not permitted to deploy on the cluster used for production.')

    app_yaml = AppYaml.get_by_app_and_name(app, app_yaml_name)
    if not app_yaml:
        abort(404, "AppYaml {} doesn't exist.".format(app_yaml_name))

    # check release
    release = app.get_release_by_tag(tag)
    if not release:
        abort(404, 'release {} not found.'.format(tag))
    if release.build_status is False:
        abort(403, "please build release first")

    with lock_app(appname):
        # check canary
        canary_info = _get_canary_info(appname, cluster)
        if canary_info['status']:
            abort(403, "please delete canary release before you deploy a new release")

        deploy_info, exist_deploy_id = {}, -1
        with handle_k8s_error("Error when get deployment"):
            k8s_deployment = KubeApi.instance().get_deployment(appname, cluster_name=cluster, ignore_404=True)
            if k8s_deployment is not None:
                deploy_info = json.loads(k8s_deployment.metadata.annotations[ANNO_DEPLOY_INFO])
                exist_deploy_id = deploy_info["deploy_id"]
                config_id = deploy_info.get("config_id")
                # check the config id in deployment and configmap are same
                # if these two value is not same, it means data inconsistent,
                # such as create a configmap in new version, but don't create deployment in correspond version
                k8s_configmap = KubeApi.instance().get_config_map(appname, cluster_name=cluster, raw=True, ignore_404=True)
                if k8s_configmap is not None:
                    config_id_in_configmap = int(k8s_configmap.metadata.annotations[ANNO_CONFIG_ID])
                    if config_id != config_id_in_configmap:
                        logger.error(f"config id in deployment and configmap is not same({config_id}: {config_id_in_configmap}")
                        abort(500, "config id in deployment and configmap are not same, this is a serious problem, please contact administrator.")

        specs = app_yaml.specs
        fix_app_spec(specs, appname, tag)

        # update specs from release
        replicas = args.get('replicas')
        cpus = args.get('cpus')
        memories = args.get('memories')

        # sometimes user may forget fo update replicas value after a scale operation,
        # so we never scale down the deployments
        if not replicas:
            replicas = specs.service.replicas
            if k8s_deployment is not None and k8s_deployment.spec.replicas > replicas:
                replicas = k8s_deployment.spec.replicas
        try:
            specs = _update_specs(specs, cpus, memories, replicas)
        except IndexError:
            abort(403, "cpus or memories' index is larger than the number of containers")

        # create deploy version
        config_id = deploy_info.get('config_id')
        if use_newest_config:
            newest_cfg = AppConfig.get_newest_config(app, cluster)
            if newest_cfg:
                config_id = newest_cfg.id

        try:
            deploy_ver = DeployVersion.create(app, tag, specs, parent_id=exist_deploy_id, cluster=cluster, config_id=config_id)
        except:
            logger.exception("can't create deploy version")
            abort(500, "internal server error")

        _validate_secret_keys(appname, specs, cluster)

        # check configmap
        _validate_configmap_keys(appname, specs, deploy_ver.app_config)

        try:
            KubeApi.instance().deploy_app(specs, deploy_ver, cluster_name=cluster)
        except KubeError as e:
            abort(403, "Deploy Error: {}".format(str(e)))
        except ApiException as e:
            abort(e.status, "Error when deploy app: {}".format(str(e)))
        except Exception as e:
            logger.exception("kubernetes error ")
            abort(500, 'kubernetes error: {}'.format(str(e)))

        OPLog.create(
            username=g.user.username,
            app_id=app.id,
            cluster=cluster,
            appname=appname,
            tag=tag,
            action=OPType.DEPLOY_APP,
        )
        return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/undeploy', methods=['DELETE'])
@use_args(OptionalClusterArgSchema())
@user_require(True)
def undeploy_app(args, appname):
    """
    if cluster is specified, then delete the deployment in specified cluster.
    if cluster is not specified, then delete deployment in all cluster.
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
    cluster = args.get('cluster', KubeApi.ALL_CLUSTER)
    app = get_app_raw(appname, [RBACAction.UNDEPLOY], cluster)
    tag = app.latest_release.tag if app.latest_release else ""

    with lock_app(appname):
        with handle_k8s_error("Error when undploy app {}".format(appname)):
            KubeApi.instance().undeploy_app(appname, app.type, ignore_404=True, cluster_name=cluster)
            OPLog.create(
                username=g.user.username,
                app_id=app.id,
                appname=appname,
                tag=tag,
                cluster=cluster,
                action=OPType.UNDEPLOY_APP,
            )

    msg = 'Warning: App **{}**\'s deployment in cluster **{}** has been deleted by **{}**.'.format(appname, cluster, g.user.nickname)
    im_sendmsg(IM_WEBHOOK_CHANNEL, msg)
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/canary/deploy', methods=['PUT'])
@use_args(DeploySchema())
@user_require(True)
def deploy_app_canary(args, appname):
    """
    deploy app canary version to kubernetes
    ---
    definitions:
      DeployArgs:
        type: object
        properties:
          cluster:
            type: string
            required: true
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
    cluster = args['cluster']
    tag = args["tag"]
    app_yaml_name = args['app_yaml_name']
    use_newest_config = args['use_newest_config']

    with lock_app(appname):
        app = get_app_raw(appname, [RBACAction.DEPLOY, ], cluster)

        if app.type != "web":
            abort(403, "Only web app can deploy canary release")

        if cluster in PROTECTED_CLUSTER and app.rank != 1:
            abort(403, 'This app is not permitted to deploy on the cluster used for production.')

        app_yaml = AppYaml.get_by_app_and_name(app, app_yaml_name)
        if not app_yaml:
            abort(404, "AppYaml {} doesn't exist.".format(app_yaml_name))

        release = app.get_release_by_tag(tag)
        if not release:
            abort(404, 'release {} not found.'.format(tag))

        if release.build_status is False:
            abort(403, "please build release first")

        specs = app_yaml.specs
        fix_app_spec(specs, appname, tag)
        # update specs from release
        replicas = args.get('replicas')
        cpus = args.get('cpus')
        memories = args.get('memories')

        if not replicas:
            replicas = specs.service.replicas
        try:
            specs = _update_specs(specs, cpus, memories, replicas)
        except IndexError:
            abort(403, "cpus or memories' index is larger than the number of containers")

        # validate secret keys
        _validate_secret_keys(appname, specs, cluster)

        if use_newest_config:
            app_cfg = AppConfig.get_newest_config(app, cluster)
        else:
            app_cfg = None
            with handle_k8s_error("Error when get deployment"):
                k8s_deployment = KubeApi.instance().get_deployment(appname, cluster_name=cluster, ignore_404=True)
                if k8s_deployment is not None:
                    deploy_info = json.loads(k8s_deployment.metadata.annotations[ANNO_DEPLOY_INFO])
                    config_id = deploy_info.get("config_id")
                    app_cfg = AppConfig.get(id=config_id)

        # validate configmap keys
        _validate_configmap_keys(appname, specs, app_cfg)
        try:
            KubeApi.instance().deploy_app_canary(specs, release.tag, app_cfg=app_cfg, cluster_name=cluster)
        except KubeError as e:
            abort(403, "Deploy Canary Error: {}".format(str(e)))
        except ApiException as e:
            abort(e.status, "Error when deploy app canary: {}".format(str(e)))
        except Exception as e:
            logger.exception("Kubernetes error ")
            abort(500, 'kubernetes error: {}'.format(str(e)))

        OPLog.create(
            username=g.user.username,
            app_id=app.id,
            appname=appname,
            cluster=cluster,
            tag=release.tag,
            action=OPType.DEPLOY_APP_CANARY,
        )
        return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/canary', methods=['DELETE'])
@use_args(ClusterArgSchema())
@user_require(True)
def undeploy_app_canary(args, appname):
    """
    delete app canary release in kubernetes
    ---
    """
    cluster = args['cluster']

    app = get_app_raw(appname, [RBACAction.UNDEPLOY, ], cluster)

    with lock_app(appname):
        canary_info = _get_canary_info(appname, cluster)
        if not canary_info['status']:
            return DEFAULT_RETURN_VALUE

        with handle_k8s_error("Error when delete app canary {}".format(appname)):
            KubeApi.instance().undeploy_app_canary(appname, cluster_name=cluster)

        OPLog.create(
            username=g.user.username,
            app_id=app.id,
            appname=appname,
            cluster=cluster,
            # tag=release.tag,
            action=OPType.UNDEPLOY_APP_CANARY,
        )
        return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/canary/weight', methods=['POST'])
@use_args(AppCanaryWeightArgSchema())
@user_require(True)
def set_app_canary_weight(args, appname):
    """
    delete app canary release in kubernetes
    ---
    """
    cluster = args['cluster']
    weight = args['weight']

    app = App.get_by_name(appname)
    if not app:
        abort(404, 'app {} not found'.format(appname))

    with lock_app(appname):
        canary_info = _get_canary_info(appname, cluster)
        if not canary_info['status']:
            abort(403, "canary release not found")

        if not g.user.granted_to_app(app):
            abort(403, 'You\'re not granted to this app, ask administrators for permission')

        with handle_k8s_error("Error when set app canary weight {}".format(appname)):
            KubeApi.instance().set_traefik_weight(appname, weight, cluster_name=cluster)

        OPLog.create(
            username=g.user.username,
            app_id=app.id,
            appname=appname,
            cluster=cluster,
            # tag=release.tag,
            action=OPType.SET_APP_CANARY_WEIGHT,
        )
        return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/canary')
@use_args(ClusterArgSchema(), location="query")
@user_require(True)
def get_app_canary_info(args, appname):
    """
    delete app canary release in kubernetes
    ---
    """
    cluster = args['cluster']

    app = get_app_raw(appname, [RBACAction.GET, ], cluster)
    return _get_canary_info(appname, cluster)


@bp.route('/<appname>/abtesting', methods=['PUT'])
@use_args(ABTestingSchema())
@user_require(True)
def set_app_abtesting_rules(args, appname):
    """
    set ABTesting rules for specified app
    ---
    definitions:
      ABTestingRules:
        type: object
        properties:
          cluster:
            type: string
            required: true
          rules:
            type: string
            required: true

    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: abtesting_args
        in: body
        required: true
        schema:
          $ref: '#/definitions/ABTestingRules'
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
    cluster = args['cluster']
    rules = args["rules"]

    app = get_app_raw(appname, [RBACAction.UPDATE, ], cluster)

    canary_info = _get_canary_info(appname, cluster)
    if not canary_info['status']:
        abort(403, "you must deploy canary version before adding abtesting rules")

    with handle_k8s_error("Error when add abtesting rules"):
        KubeApi.instance().set_abtesting_rules(appname, rules, cluster_name=cluster)
    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/abtesting')
@use_args(ClusterArgSchema(), location="query")
@user_require(True)
def get_app_abtesting_rules(args, appname):
    """
    set ABTesting rules for specified app
    ---
    definitions:
      ABTestingRules:
        type: object
        properties:
          cluster:
            type: string
            required: true

    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: abtesting_args
        in: body
        required: true
        schema:
          $ref: '#/definitions/ABTestingRules'
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
    cluster = args['cluster']

    app = get_app_raw(appname, [RBACAction.GET, ], cluster)

    with handle_k8s_error("Error when get abtesting rules"):
        rules = KubeApi.instance().get_abtesting_rules(appname, cluster_name=cluster)

    if rules is None:
        abort(404, "not found")
    return rules


@bp.route('/<appname>/container/stop', methods=['POST'])
@use_args(PodEntryArgsSchema())
@user_require(True)
def stop_container(args, appname):
    """
    stop container
    """
    podname = args['podname']
    cluster = args['cluster']
    container = args.get('container', None)

    app = get_app_raw(appname, [RBACAction.STOP_CONTAINER, ], cluster)

    with handle_k8s_error("Error when stop container"):
        rules = KubeApi.instance().stop_container(podname, cluster_name=cluster, container=container)

    return DEFAULT_RETURN_VALUE


@bp.route('/<appname>/build/kill', methods=['DELETE'])
@user_require(True)
def kill_build_task(appname):
    """
    kill build task
    """
    app = App.get_by_name(appname)
    if not app:
        abort(404, 'app {} not found'.format(appname))

    if not g.user.granted_to_app(app):
        abort(403, 'You\'re not granted to this app, ask administrators for permission')

    app_redis_key = make_app_redis_key(appname)
    try:
        build_task_id = rds.hget(app_redis_key, "build-task-id")
        if not build_task_id:
            abort(404, "build task is not running")
        if isinstance(build_task_id, bytes):
            build_task_id = build_task_id.decode('utf8')
        from console.app import celery
        # logger.debug("++++++++", build_task_id, celery.control)
        celery.control.revoke(build_task_id, terminate=True)

        # notify build greenlet to exit
        channel_name = TASK_PUBSUB_CHANNEL.format(task_id=build_task_id)
        failure_msg = make_errmsg('terminate by user', jsonize=True)
        rds.publish(channel_name, failure_msg)
        rds.publish(channel_name, TASK_PUBSUB_EOF.format(task_id=build_task_id))
    finally:
        rds.hdel(app_redis_key, "build-task-id")
    return DEFAULT_RETURN_VALUE
