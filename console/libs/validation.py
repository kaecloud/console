# -*- coding: utf-8 -*-
import re
from humanfriendly import parse_size, InvalidSize
from marshmallow import fields, validates_schema, ValidationError
from numbers import Number

from console.models.base import StrictSchema
from console.libs.k8s import kube_api


def validate_jobname(name):
    regex = re.compile(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?$')
    if regex.match(name) is None:
        raise ValidationError("jobname is invalid")


def validate_username(n):
    from console.models.user import User
    if not bool(User.get_by_username(n)):
        raise ValidationError('User {} not found, needs to login first'.format(n))


def validate_user_id(id_):
    from console.models.user import User
    if not bool(User.get(id_)):
        raise ValidationError('User {} not found, needs to login first'.format(id_))


def validate_secret_data(dd):
    for k, v in dd.items():
        if not isinstance(v, (bytes, str)):
            raise ValidationError("value of secret should be string or bytes")
        if not isinstance(k, (bytes, str)):
            raise ValidationError("key of secret should be string or bytes")


def validate_cpu(d):
    for k, v in d.items():
        if k not in ('request', "limit"):
            raise ValidationError("cpu dict's key should be request or limit")
        try:
            if v[-1] == 'm':
                v = v[:-1]
            if float(v) < 0:
                raise ValidationError('CPU must >=0')
        except:
            raise ValidationError("invalid cpu value format")


def validate_memory(d):
    for k, v in d.items():
        if k not in ('request', "limit"):
            raise ValidationError("memory dict's key should be request or limit")
        try:
            if parse_size(v) <= 0:
                raise ValidationError("memory should bigger than zero")
        except InvalidSize:
            raise ValidationError("invalid memory value format")


def validate_memory_dict(dd):
    for idx, v in dd.items():
        if idx != "*" and not isinstance(idx, int):
            raise ValidationError("{}'s key should be '*' or an integer")
        if not isinstance(v, dict):
            raise ValidationError("{} is not a dict".format(v))
        validate_memory(v)


def validate_cpu_dict(dd):
    for idx, v in dd.items():
        if idx != "*" and not isinstance(idx, int):
            raise ValidationError("{}'s key should be '*' or an integer")
        if not isinstance(v, dict):
            raise ValidationError("{} is not a dict".format(v))
        validate_cpu(v)


def validate_cluster_name(cluster):
    if kube_api.cluster_exist(cluster) is False:
        raise ValidationError("cluster {} not exists".format(cluster))


class RegisterSchema(StrictSchema):
    appname = fields.Str(required=True)
    tag = fields.Str(required=True)
    git = fields.Str(required=True)
    specs_text = fields.Str(required=True)
    branch = fields.Str()
    commit_message = fields.Str()
    author = fields.Str()
    force = fields.Bool(missing=False)


def parse_memory(s):
    if isinstance(s, Number):
        return int(s)
    return parse_size(s, binary=True)


def parse_cpu(ss):
    pass


class SimpleNameSchema(StrictSchema):
    name = fields.Str(required=True)


class UserSchema(StrictSchema):
    username = fields.Str(validate=validate_username)
    user_id = fields.Int(validate=validate_user_id)
    email = fields.Email()

    @validates_schema(pass_original=True)
    def further_check(self, _, original_data):
        if not original_data:
            raise ValidationError('Must provide username, user_id or email, got nothing')


class DeploySchema(StrictSchema):
    cluster = fields.Str(required=True, validate=validate_cluster_name)
    tag = fields.Str(required=True)
    specs_text = fields.Str()
    cpus = fields.Dict(validate=validate_cpu_dict)
    memories = fields.Dict(validate=validate_memory_dict)
    replicas = fields.Int()
    debug = fields.Bool(missing=False)


class ScaleSchema(StrictSchema):
    cluster = fields.Str(required=True, validate=validate_cluster_name)
    cpus = fields.Dict(validate=validate_cpu_dict)
    memories = fields.Dict(validate=validate_memory_dict)
    replicas = fields.Int()

    @validates_schema(pass_original=True)
    def further_check(self, data, original_data):
        if not original_data:
            raise ValidationError('Must provide username, user_id or email, got nothing')
        cpus = data.get("cpus")
        memories = data.get("memories")
        replicas = data.get("replicas")
        if not (cpus or memories or replicas):
            raise ValidationError('you must at least specify one of cpu, memory, replica ')


class BuildArgsSchema(StrictSchema):
    tag = fields.Str(required=True)


class ClusterArgSchema(StrictSchema):
    cluster = fields.Str(required=True, validate=validate_cluster_name)


class SecretSchema(StrictSchema):
    cluster = fields.Str(required=True, validate=validate_cluster_name)
    data = fields.Dict(required=True, validate=validate_secret_data)


class ConfigMapSchema(StrictSchema):
    cluster = fields.Str(required=True, validate=validate_cluster_name)
    data = fields.Str(required=True)
    config_name = fields.Str(default='config')


class RollbackSchema(StrictSchema):
    cluster = fields.Str(required=True, validate=validate_cluster_name)
    revision = fields.Int(missing=0)


class JobArgsSchema(StrictSchema):
    jobname = fields.Str(validate=validate_jobname)
    git = fields.Str()
    branch = fields.Str(missing='master')
    commit = fields.Str()
    comment = fields.Str()

    shell = fields.Bool(missing=False)

    image = fields.Str()
    command = fields.Str()
    gpu = fields.Int()
    autoRestart = fields.Bool(missing=False)

    specs_text = fields.Str()

    @validates_schema(pass_original=True)
    def further_check(self, data, original_data):
        if 'specs_text' not in data:
            required_fields = (
                'jobname', 'image',
                'command'
            )
            for field in required_fields:
                if field not in original_data:
                    raise ValidationError("{} is required when specs_text is null".format(field))


register_schema = RegisterSchema()
deploy_schema = DeploySchema()
scale_schema = ScaleSchema()
build_args_schema = BuildArgsSchema()
secret_schema = SecretSchema()
config_map_schema = ConfigMapSchema()
