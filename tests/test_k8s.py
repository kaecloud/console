import pytest
from pprint import pprint
import yaml
from console.k8s.k8s import KubernetesApi
from console.models.specs import specs_schema

specs_text = """
appname: nginx              
git: yangyu0.github.com             

builds:                              
  nginx:                           
    tag: v2.0                       
    dockerfile: Dockerfile-alternate 

services:
  web:                    
    user: root               
    type: worker             
    replicas: 3              
    labels:                  
      - proctype=router

    mountpoints:               
      - a.external.domain1/b/c  
    ports:                    
    - 80/tcp            

    containers:
    - name: nginx
      image: nginx:1.7.9
      ports:
      - containerPort: 80
"""


# def test_specs():
#     dic = yaml.load(specs_text)
#     unmarshal_result = specs_schema.load(dic)
#     d = unmarshal_result.data
#     pprint(d, d.services)


def test_deployment():
    dic = yaml.load(specs_text)
    unmarshal_result = specs_schema.load(dic)
    d = unmarshal_result.data
    print(d)
    print(d.services)
    deploy, svc, ing = KubernetesApi.create_resource_dict(d)
    pprint(deploy)
    api = KubernetesApi(use_kubeconfig=True)
    api.apply(deploy[0])
