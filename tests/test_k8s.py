import pytest
from pprint import pprint
import yaml
from console.libs.k8s import KubernetesApi, ApiException
from console.libs.specs import app_specs_schema

specs_text = """
appname: hello
git: yangyu0.github.com

builds:
- name: nginx:
  tag: v2.0
  dockerfile: Dockerfile-alternate

service:
  user: root
  type: web
  replicas: 2
  labels:
    - proctype=router

  mountpoints:
    - a.external.domain1/b/c
  ports:
  - port: 80
    targetPort: 8080

  configDir: /tmp/configmap
  secrets:
    envNameList: ["USERNAME", "PASSWORD"]
    secretKeyList: ["username", "password"]

  containers:
  - name: hello-world
    image: yuyang0/hello-world
    ports:
    - containerPort: 8080
"""


def compare_dict(d1, d2):
    shared = set(d1.items()) - set(d2.items())
    return len(shared) == 0


def test_specs():
    dic = yaml.load(specs_text)
    unmarshal_result = app_specs_schema.load(dic)
    d = unmarshal_result.data
    pprint(d, d.services)


# def test_deploy():
#     dic = yaml.load(specs_text)
#     unmarshal_result = specs_schema.load(dic)
#     d = unmarshal_result.data
#     appname = d.appname
#
#     api = KubernetesApi(use_kubeconfig=True)
#     dd = {
#         "username": "root",
#         "password": "1234567",
#     }
#     api.create_or_update_secret(appname, dd)
#
#     data = """
# aaa = 1
# bbb = 1
# ccc = 2
#     """
#     api.create_or_update_config_map(appname, data)
#
#     dic = yaml.load(specs_text)
#     unmarshal_result = specs_schema.load(dic)
#     d = unmarshal_result.data
#     api.deploy_app(d)


# def test_service():
#     dic = yaml.load(specs_text)
#     unmarshal_result = specs_schema.load(dic)
#     d = unmarshal_result.data
#     deploy, svc, ing = KubernetesApi.create_resource_dict(d)
#     pprint(svc)
#     api = KubernetesApi(use_kubeconfig=True)
#     ret = api.update_service(svc[0])
#     pprint(ret)

#
#
# def test_ingress():
#     pass


def test_secret():
    appname = "abcdefg"
    api = KubernetesApi()
    dd = {
        "username": "root",
        "password": "123456",
    }
    api.create_or_update_secret(appname, dd)
    real_dd = api.get_secret(appname)
    assert compare_dict(dd, real_dd) is True

    # update
    dd = {
        "key1": "val1",
        "key2": "val2",
    }
    api.create_or_update_secret(appname, dd)
    real_dd = api.get_secret(appname)
    assert compare_dict(dd, real_dd) is True
    api.delete_secret(appname)

    with pytest.raises(ApiException) as e:
        api.delete_secret(appname)
    assert e.value.status == 404


def test_config_map():
    appname = "abcdefg"
    data = """
    aaa = 1
    bbb = 1
    ccc = 2
    """
    api = KubernetesApi()
    api.create_or_update_config_map(appname, data)
    cfg = api.get_config_map(appname)
    assert cfg == data

    # update
    data = """
    aaa = 1
    bbb = 1
    ccc = 2
    ddd = 678
    """
    api = KubernetesApi()
    api.create_or_update_config_map(appname, data)
    cfg = api.get_config_map(appname)
    assert cfg == data

    api.delete_config_map(appname)

    with pytest.raises(ApiException) as e:
        api.delete_config_map(appname)
    assert e.value.status == 404
