# -*- coding: utf-8 -*-

import os
from addict import Dict
from marshmallow import fields, validates_schema, ValidationError, post_load

from console.models.base import StrictSchema
from console.libs.validation import validate_cpu, validate_memory, validate_jobname
from console.config import DEFAULT_REGISTRY


def validate_port(n):
    if not 0 < n <= 65535:
        raise ValidationError('Port must be 0-65,535')


def validate_protocol(ss):
    if ss not in ("TCP", "UDP"):
        raise ValidationError("protocol should be TCPor UDP")


def validate_env_list(l):
    for env in l:
        if len(env.split("=")) != 2:
            raise ValidationError("environment should conform to format: key=val")


def validate_image_pull_policy(ss):
    if ss not in ('Always', 'Never', 'IfNotPresent'):
        raise ValidationError("invalid imagePullPolicy value, only one of Always, Never, IfNotPresent is allowed")


def validate_app_type(ss):
    if ss not in ("web", "worker", "job"):
        raise ValidationError("app type should be `web`, `worker`, `job`")


def validate_abs_path(ss):
    if not os.path.isabs(ss):
        raise ValidationError("{} is not an absolute path".format(ss))


def validate_abs_path_list(lst):
    for l in lst:
        if l[0] != '/':
            raise ValidationError("{} is not a absolute path".format(l))


def validate_mountpoints(lst):
    hosts = set()
    for mp in lst:
        parts = mp.split('/', 1)
        host = parts[0]
        if host in hosts:
            raise ValidationError('{} duplicate domain.'.format(host))
        hosts.add(host)


def validate_build_name(name):
    if ':' in name:
        raise ValidationError("build name can't contain `:`.")


def validate_update_strategy_type(ss):
    if ss not in ("RollingUpdate", "Recreate"):
        raise ValidationError("strategy type must be RollingUpdate or Recreate")


def validate_percentage_or_int(ss):
    if ss.endswith('%'):
        ss = ss[:-1]
    try:
        if int(ss) < 0:
            raise ValidationError("must be positive number or zero.")
    except ValueError:
        raise ValidationError("invalid percentage or integer")


class ContainerPort(StrictSchema):
    containerPort = fields.Int(validate=validate_port)
    hostIP = fields.Str()
    hostPort = fields.Int(validate=validate_port)
    name = fields.Str()
    protocol = fields.Str(validate=validate_protocol)


class BuildSchema(StrictSchema):
    name = fields.Str(validate=validate_build_name)
    tag = fields.Str()
    dockerfile = fields.Str()
    target = fields.Str()
    args = fields.Dict()


class RollingUpdate(StrictSchema):
    maxSurge = fields.Str(missing="25%", validate=validate_percentage_or_int)
    maxUnavailable = fields.Str(missing="25%", validate=validate_percentage_or_int)


class UpdateStrategy(StrictSchema):
    type = fields.Str(missing="RollingUpdate", validate=validate_update_strategy_type)
    # only valid when type is RollingUpdate
    rollingUpdate = fields.Nested(RollingUpdate)


build_schema = BuildSchema()


class ContainerSpec(StrictSchema):
    name = fields.Str()
    image = fields.Str()
    imagePullPolicy = fields.Str(validate=validate_image_pull_policy)
    args = fields.List(fields.Str())
    command = fields.List(fields.Str())
    env = fields.List(fields.Str(), validate=validate_env_list)
    tty = fields.Bool()
    workingDir = fields.Str(validate=validate_abs_path)
    livessProbe = fields.Dict()
    readinessProbe = fields.Dict()
    ports = fields.List(fields.Nested(ContainerPort))

    cpu = fields.Dict(validate=validate_cpu)
    memory = fields.Dict(validate=validate_memory)
    gpu = fields.Int()

    volumes = fields.List(fields.Str(), validate=validate_abs_path_list)
    dfsVolumes = fields.List(fields.Str(), validate=validate_abs_path_list)
    configDir = fields.Str()
    secrets = fields.Dict()


class ServicePort(StrictSchema):
    port = fields.Int(required=True, validate=validate_port)
    targetPort = fields.Int(validate=validate_port)
    protocol = fields.Str(validate=validate_protocol, missing="TCP")


class ServiceSchema(StrictSchema):
    user = fields.Str(missing="root")
    labels = fields.List(fields.Str())
    mountpoints = fields.List(fields.Str(), validate=validate_mountpoints, missing=[])
    ports = fields.List(fields.Nested(ServicePort))

    replicas = fields.Int(missing=1)
    minReadySeconds = fields.Int()
    progressDeadlineSeconds = fields.Int()
    strategy = fields.Nested(UpdateStrategy)

    containers = fields.List(fields.Nested(ContainerSpec), required=True)


service_schema = ServiceSchema()


class SpecsSchema(StrictSchema):
    appname = fields.Str(required=True)
    type = fields.Str(missing="worker", validate=validate_app_type)
    git = fields.Str(required=True)
    builds = fields.List(fields.Nested(BuildSchema), missing=[])
    service = fields.Nested(ServiceSchema, required=True)

    @post_load
    def finalize(self, data):
        """add defaults to fields, and then construct a Box"""
        return Dict(data)

    @validates_schema
    def validate_misc(self, data):
        # check raw related fields
        pass


specs_schema = SpecsSchema()


def load_specs(raw_data, tag):
    """
    add defaults to fields, and then construct a Dict
    :param raw_data:
    :param tag: release tag
    :return:
    """
    data = specs_schema.load(raw_data).data
    appname = data['appname']
    build_names = set()
    for build in data["builds"]:
        name = build.get("name", None)
        if name:
            if name in build_names:
                raise ValidationError("duplicate build name")
            build_names.add(name)
            if name == appname:
                # overwrite the tag the release tag
                build['tag'] = tag
    default_release_image = None
    if appname in build_names:
        default_release_image = "{}/{}:{}".format(DEFAULT_REGISTRY.rstrip('/'), appname, tag)

    if data["type"] == "web":
        ports = data["service"]["ports"]
        if len(ports) != 1:
            ValidationError("web service should contain only one port")
        for p in ports:
            if p["port"] != 80:
                raise ValidationError("port of web service must be 80")
    containers = data["service"]["containers"]
    for container in containers:
        if "image" not in container:
            if not default_release_image:
                raise ValidationError("you must set image for container")
            container["image"] = default_release_image

    return Dict(data)


class JobSchema(StrictSchema):
    jobname = fields.Str(required=True, validate=validate_jobname)
    # the below 4 fields are not used by kubernetes
    git = fields.Str()
    branch = fields.Str(missing='master')
    commit = fields.Str()
    comment = fields.Str()

    backoffLimit = fields.Int()
    completions = fields.Int()

    parallelism = fields.Int()
    autoRestart = fields.Bool(missing=False)

    containers = fields.List(fields.Nested(ContainerSpec), required=True)

    @post_load
    def finalize(self, data):
        """add defaults to fields, and then construct a Box"""
        return Dict(data)


job_schema = JobSchema()


def load_job_specs(raw_data):
    """
    add defaults to fields, and then construct a Dict
    :param raw_data:
    :param tag: release tag
    :return:
    """
    data = job_schema.load(raw_data).data
    return Dict(data)
