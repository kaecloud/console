# -*- coding: utf-8 -*-
from humanfriendly import parse_size
from marshmallow import fields, validates_schema, ValidationError
from numbers import Number

from console.config import ZONE_CONFIG
from console.models.base import StrictSchema


def validate_username(n):
    from console.models.user import User
    if not bool(User.get_by_name(n)):
        raise ValidationError('User {} not found, needs to login first'.format(n))


def validate_user_id(id_):
    from console.models.user import User
    if not bool(User.get(id_)):
        raise ValidationError('User {} not found, needs to login first'.format(id_))


def validate_sha(s):
    if len(s) < 7:
        raise ValidationError('minimum sha length is 7')


def validate_full_sha(s):
    if len(s) < 40:
        raise ValidationError('must be length 40')


def validate_zone(s):
    if s not in ZONE_CONFIG:
        raise ValidationError('Bad zone: {}'.format(s))


def validate_full_contianer_id(s):
    if len(s) < 64:
        raise ValidationError('Container ID must be of length 64')


class RegisterSchema(StrictSchema):
    appname = fields.Str(required=True)
    sha = fields.Str(required=True, validate=validate_full_sha)
    git = fields.Str(required=True)
    specs_text = fields.Str(required=True)
    branch = fields.Str()
    git_tag = fields.Str()
    commit_message = fields.Str()
    author = fields.Str()


def parse_memory(s):
    if isinstance(s, Number):
        return int(s)
    return parse_size(s, binary=True)


class SimpleNameSchema(StrictSchema):
    name = fields.Str(required=True)


class ComboSchema(StrictSchema):
    name = fields.Str(required=True)
    entrypoint_name = fields.Str(required=True)
    podname = fields.Str(required=True)
    nodename = fields.Str()
    extra_args = fields.Str()
    networks = fields.List(fields.Str(), required=True)
    cpu_quota = fields.Float(required=True)
    memory = fields.Function(deserialize=parse_memory, required=True)
    count = fields.Int(missing=1)
    envname = fields.Str()


class UserSchema(StrictSchema):
    username = fields.Str(validate=validate_username)
    user_id = fields.Int(validate=validate_user_id)

    @validates_schema(pass_original=True)
    def further_check(self, _, original_data):
        if not original_data:
            raise ValidationError('Must provide username or user_id, got nothing')


class DeploySchema(StrictSchema):
    appname = fields.Str(required=True)
    zone = fields.Str(required=True)
    sha = fields.Str(required=True, validate=validate_sha)
    combo_name = fields.Str(required=True)
    entrypoint_name = fields.Str()
    podname = fields.Str()
    nodename = fields.Str()
    extra_args = fields.Str()
    networks = fields.List(fields.Str())
    cpu_quota = fields.Float()
    memory = fields.Function(deserialize=parse_memory)
    count = fields.Int()
    debug = fields.Bool(missing=False)


class RenewSchema(StrictSchema):
    container_ids = fields.List(fields.Str(required=True, validate=validate_full_contianer_id), required=True)
    sha = fields.Str(validate=validate_sha)


class DeployELBSchema(StrictSchema):
    name = fields.Str(required=True)
    zone = fields.Str(required=True)
    sha = fields.Str(required=True, validate=validate_sha)
    combo_name = fields.Str(required=True)
    nodename = fields.Str()


class GetContainerSchema(StrictSchema):
    appname = fields.Str()
    sha = fields.Str(validate=validate_sha)
    container_id = fields.Str(validate=validate_sha)
    entrypoint_name = fields.Str()
    cpu_quota = fields.Float()
    memory = fields.Function(deserialize=parse_memory)
    zone = fields.Str(validate=validate_zone)
    podname = fields.Str()
    nodename = fields.Str()

    @validates_schema
    def further_check(self, data):
        if not data:
            raise ValidationError('dude what? you did not specify any query parameters')


class BuildArgsSchema(StrictSchema):
    appname = fields.Str(required=True)
    sha = fields.Str(required=True, validate=validate_sha)


class RemoveContainerSchema(StrictSchema):
    container_ids = fields.List(fields.Str(required=True, validate=validate_full_contianer_id), required=True)


class CreateELBRulesSchema(StrictSchema):
    appname = fields.Str(required=True)
    podname = fields.Str(required=True)
    entrypoint_name = fields.Str(required=True)
    domain = fields.Str(required=True)
    arguments = fields.Dict(default={})


deploy_schema = DeploySchema()
renew_schema = RenewSchema()
deploy_elb_schema = DeployELBSchema()
build_args_schema = BuildArgsSchema()
remove_container_schema = RemoveContainerSchema()
