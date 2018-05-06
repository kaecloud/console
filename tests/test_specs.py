# -*- coding: utf-8 -*-

import pytest
from marshmallow import ValidationError
import yaml
from console.models.specs import ContainerSpec, specs_schema, UpdateStrategy


# def test_container_spec():
#     container_spec = ContainerSpec()
#     container_spec.load(yaml.load(default_container))
#     yaml_dict = yaml.load(default_specs_text)
#     yaml_dict['service']['containers'] = [yaml.load(default_container)]
#     spec = specs_schema.load(yaml_dict).data
#     api = KubernetesApi(use_kubeconfig=True)
#     d, s, i = api.create_resource_dict(spec)
#     pprint.pprint(d[0].to_dict())

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
