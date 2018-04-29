# -*- coding: utf-8 -*-

from box import Box
from humanfriendly import InvalidTimespan, parse_timespan, parse_size
from marshmallow import fields, validates_schema, ValidationError, post_load
from numbers import Number

from console.models.base import StrictSchema


def validate_port(n):
    if not 0 < n <= 65535:
        raise ValidationError('Port must be 0-65,535')


def validate_service_name(s):
    if '.' in s:
        raise ValidationError('Entrypoints must not contain underscore')


def validate_cpu(n):
    if n < 0:
        raise ValidationError('CPU must >=0')


def validate_kv_line(ss):
    if len(ss.split("=")) != 2:
        raise ValidationError("environment should conform to format: key=val")


def parse_builds(dic):
    '''
    `builds` clause within app.yaml contains None or several build stages, this
    function validates every stage within
    '''
    for stage_name, build in dic.items():
        unmarshal_result = build_schema.load(build)
        dic[stage_name] = unmarshal_result.data

    return dic


def parse_memory(s):
    return parse_size(s, binary=True) if isinstance(s, str) else s


def parse_services(dic):
    for service_name, service_dic in dic.items():
        validate_service_name(service_name)
        unmarshal_result = service_schema.load(service_dic)
        dic[service_name] = unmarshal_result.data

    return dic


def parse_containers(ls):
    #TODO adjust volumes
    return ls


class BuildSchema(StrictSchema):
    tag = fields.Str()
    dockerfile = fields.Str()
    target = fields.Str()
    args = fields.Dict()


build_schema = BuildSchema()


class ServiceSchema(StrictSchema):
    user = fields.Str(missing="root")
    type = fields.Str(missing="worker")
    replicas = fields.Int(missing=1)
    labels = fields.List(fields.Str())
    mountpoints = fields.List(fields.Str())
    ports = fields.List(fields.Str())

    containers = fields.Function(deserialize=parse_containers, required=True)


service_schema = ServiceSchema()


class SpecsSchema(StrictSchema):
    appname = fields.Str(required=True)
    git = fields.Str(required=True)
    services = fields.Function(deserialize=parse_services, required=True)
    builds = fields.Function(deserialize=parse_builds, required=True)

    @post_load
    def finalize(self, data):
        """add defaults to fields, and then construct a Box"""
        # for service in data['services'].values():
        #     # set default working_dir to app's home
        #     if not service.get('workingDir'):
        #         service['working_dir'] = '/home/{}'.format(data['appname'])

        return Box(data, conversion_box=False, default_box=True,
                   default_box_attr=None, frozen_box=True)

    @validates_schema
    def validate_misc(self, data):
        # check raw related fields
        pass


specs_schema = SpecsSchema()
