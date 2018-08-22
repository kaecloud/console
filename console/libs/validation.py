# -*- coding: utf-8 -*-
import re
import numbers
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
        if not k:
            raise ValidationError("key must not be empty")
        if not v:
            raise ValidationError("value must not be empty")
        if not isinstance(v, (bytes, str)):
            raise ValidationError("value of secret should be string or bytes")
        if not isinstance(k, (bytes, str)):
            raise ValidationError("key of secret should be string or bytes")


def validate_abtesting_rules(dd):
    """
    the format of  dd is:
    {
        "domain1": {
            "type": "ua",
            "op": "regex",
            "op_args": "xxx",
            "get_args": "xxx"
        }
    }
    :param dd:
    :return:
    """
    def check_op(op, op_args):
        if op in ('equal', 'not_equal'):
            if not (isinstance(op_args, str) or isinstance(op_args, numbers.Number)):
                raise ValidationError("invalid argument for {} op, only string and numbers are allowed".format(op))
        elif op in ('regex', 'not_regex'):
            if not isinstance(op_args, str):
                raise ValidationError("op argument {} is not regex pattern".format(op_args))
        elif op in ('range', 'not_range'):
            if (not isinstance(op_args, dict)) or \
                "start" not in op_args or \
                "end" not in op_args:
                raise ValidationError("invalid argument for {} op".format(op))
            if op_args["start"] >= op_args["end"]:
                raise ValidationError("the left bound can't bigger than the right bound of RANG")
        elif op in ('oneof', "not_oneof"):
            if not (isinstance(op_args, list) or isinstance(op_args, tuple)):
                raise ValidationError("invalid argument for {} op, only list and tuple are allowed".format(op))
        else:
            raise ValidationError("unknown op {}".format(op))

    def check_ua(op, op_args, get_args):
        if op not in ('equal', 'not_equal', 'regex', 'not_regex', 'oneof', 'not_oneof'):
            raise ValidationError("invalid op for ua rule, only equal, regex, oneof and its reverse op are allowed")
        check_op(op, op_args)

    def check_ip(op, op_args, get_args):
        if op not in ('equal', 'not_equal', 'range', 'not_range', 'oneof', 'not_oneof'):
            raise ValidationError("invalid op for ip rule, only equal, range, oneof and its reverse op are allowed")
        check_op(op, op_args)

    def check_header(op, op_args, get_args):
        if not isinstance(get_args, str):
            raise ValidationError("get argument for header op must be a string")
        check_op(op, op_args)

    def check_cookie(op, op_args, get_args):
        if not isinstance(get_args, str):
            raise ValidationError("get argument for cookie op must be a string")
        check_op(op, op_args)

    def check_query(op, op_args, get_args):
        if not isinstance(get_args, str):
            raise ValidationError("get argument for query op must be a string")
        check_op(op, op_args)

    if len(dd) == 0:
        raise ValidationError("abtesting rules is empty")
    for k, v in dd.items():
        try:
            ty = v["type"]
            op = v["op"]
            op_args = v["op_args"]
        except KeyError:
            raise ValidationError("abtesting rule must contain type, op and op_args")
        get_args = v.get("get_args", None)
        validator_map = {
            "ua": check_ua,
            "ip": check_ip,
            "header": check_header,
            "cookie": check_cookie,
            "query": check_query,
        }
        if ty not in validator_map:
            raise ValidationError("invalid rule type")
        validator_map[ty](op, op_args, get_args)


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


class SpecsArgsSchema(StrictSchema):
    specs_text = fields.Str(required=True)


class ClusterCanarySchema(StrictSchema):
    cluster = fields.Str(required=True, validate=validate_cluster_name)
    canary = fields.Bool(missing=False)


class ABTestingSchema(StrictSchema):
    cluster = fields.Str(required=True, validate=validate_cluster_name)
    rules = fields.Dict(required=True, validate=validate_abtesting_rules)


class SecretSchema(StrictSchema):
    cluster = fields.Str(required=True, validate=validate_cluster_name)
    data = fields.Dict(required=True, validate=validate_secret_data)


class ConfigMapSchema(StrictSchema):
    cluster = fields.Str(required=True, validate=validate_cluster_name)
    data = fields.Str(required=True)
    config_name = fields.Str(missing='config')


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


cluster_args_schema = ClusterArgSchema()
cluster_canary_schema = ClusterCanarySchema()
register_schema = RegisterSchema()
deploy_schema = DeploySchema()
scale_schema = ScaleSchema()
build_args_schema = BuildArgsSchema()
secret_schema = SecretSchema()
config_map_schema = ConfigMapSchema()
