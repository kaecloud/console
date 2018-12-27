# -*- coding: utf-8 -*-

import copy
import pytest
from marshmallow import ValidationError
import yaml
from console.libs.specs import UpdateStrategy, ConfigMapSchema, SecretSchema


# def test_container_spec():
#     container_spec = ContainerSpec()
#     container_spec.load(yaml.load(default_container))
#     yaml_dict = yaml.load(default_specs_text)
#     yaml_dict['service']['containers'] = [yaml.load(default_container)]
#     spec = specs_schema.load(yaml_dict).data
#     api = KubernetesApi(use_kubeconfig=True)
#     d, s, i = api.create_resource_dict(spec)
#     pprint.pprint(d[0].to_dict())

def test_configmap():
    tmpl = """
dir: /dir1
key: key1
filename: name1
    """
    initial_dic = yaml.load(tmpl)
    schema = ConfigMapSchema()

    dic = copy.deepcopy(initial_dic)
    data = schema.load(dic).data
    assert data['dir'] == '/dir1'

    # missing filename case
    dic = copy.deepcopy(initial_dic)
    dic.pop('filename')
    data = schema.load(dic).data
    assert data['dir'] == '/dir1'
    assert data['filename'] == data['key'] == 'key1'

    # missing field
    with pytest.raises(ValidationError):
        schema.load({'dir': '/ddd'})

    # dir is not a absolute path
    with pytest.raises(ValidationError):
        schema.load({'dir': 'ddd', "key": "key1"})


def test_secrets():
    tmpl = """
envNameList: 
  - aa
  - bb
keyList:
  - key1
  - key2
    """
    initial_dic = yaml.load(tmpl)
    schema = SecretSchema()

    dic = copy.deepcopy(initial_dic)
    data = schema.load(dic).data
    assert data['envNameList'] == ['aa', 'bb'] and data['keyList'] == ['key1', 'key2']

    dic = copy.deepcopy(initial_dic)
    dic.pop('keyList')
    data = schema.load(dic).data
    assert data['envNameList'] == data['keyList'] == ['aa', 'bb']

    with pytest.raises(ValidationError) as e:
        _ = schema.load({"envNameList": ["aa", "bb"], "keyList": []}).data


def test_update_strategy():
    tmpl = """
type: RollingUpdate
rollingUpdate:
  maxSurge: 25%
  maxUnavailable: 35%
    """
    dic = yaml.load(tmpl)
    schema = UpdateStrategy()
    data = schema.load(dic).data
    assert data['rollingUpdate']['maxSurge'] == '25%'

    # maxSurge, maxUnavailable validate
    dic['rollingUpdate']['maxSurge'] = '2a5%'
    with pytest.raises(ValidationError) as e:
        _ = schema.load(dic).data
    dic['rollingUpdate']['maxSurge'] = '-25%'
    with pytest.raises(ValidationError) as e:
        _ = schema.load(dic).data

    # type validate
    with pytest.raises(ValidationError) as e:
        _ = schema.load({"type": "hahahn"}).data
