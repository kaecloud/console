# -*- coding: utf-8 -*-
from humanfriendly import parse_size, InvalidSize
from marshmallow import fields, validates_schema, ValidationError
from numbers import Number

from console.models.base import StrictSchema


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


class RegisterSchema(StrictSchema):
    appname = fields.Str(required=True)
    tag = fields.Str(required=True)
    git = fields.Str(required=True)
    specs_text = fields.Str(required=True)
    branch = fields.Str()
    commit_message = fields.Str()
    author = fields.Str()


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
    tag = fields.Str(required=True)
    specs_text = fields.Str()
    cpus = fields.Dict(validate=validate_cpu_dict)
    memories = fields.Dict(validate=validate_memory_dict)
    replicas = fields.Int()
    debug = fields.Bool(missing=False)


class ScaleSchema(StrictSchema):
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


class SecretSchema(StrictSchema):
    data = fields.Dict(required=True, validate=validate_secret_data)


class ConfigMapSchema(StrictSchema):
    data = fields.Str(required=True)
    config_name = fields.Str(default='config')


class RollbackSchema(StrictSchema):
    revision = fields.Int(missing=0)


class RunJobSchema(StrictSchema):
    jobname = fields.Str()
    image = fields.Str()
    command = fields.Str()
    specs_text = fields.Str()
    cpu = fields.Float()
    memory = fields.Function(deserialize=parse_memory)
    count = fields.Int()

    @validates_schema(pass_original=True)
    def further_check(self, data, original_data):
        pass



register_schema = RegisterSchema()
deploy_schema = DeploySchema()
scale_schema = ScaleSchema()
build_args_schema = BuildArgsSchema()
secret_schema = SecretSchema()
config_map_schema = ConfigMapSchema()
