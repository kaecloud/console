# -*- coding: utf-8 -*-

import copy
import random
import string
import yaml

from kaelib.spec import app_specs_schema


default_appname = "test-app"
default_git = "https://github.com/kaecloud/hello-world.git"
default_tag = "v0.0.1"

default_builds = """
default_build:                            # image name
  tag: {TAG}                        # default is the git sha
  dockerfile: Dockerfile-alternate  # optional, default is {REPO}/Dockerfile
  target: {TARGET}                  # optional, for multi-stage build
  args:                             # optional
    buildno: 1
"""

default_container = """
name: "xxx"
image: yuyang0/hello-world
imagePullPolicy: Always
args: ["xx", "xx"]
command: ['hahah']

env:                     # environments
  - ENVA=a
  - ENVB=b
tty: false               # whether allocate tty
workingDir: xxx          # working dir
cpu:
  limit: 0.5m
memory:
  request: 1.2G

ports:
  - containerPort: 9506
    protocol: TCP
    hostIP: xxx
    hostPort: 12345
    name: xxx
volumes:
  - /var/log
  - /etc/nginx/nginx.conf

configDir: /tmp/configmap
secrets:
  envNameList: ["USERNAME", "PASSWORD"]
  secretKeyList: ["username", "password"]
"""


default_specs_text = """
appname: hello
type: web
builds:
- name: hello
service:
  user: root
  replicas: 2
  labels:
    - proctype=router
  mountpoints:
    - host: test.kae.com
      path: /
  ports:
  - port: 80
    targetPort: 8080

  containers:
  - name: hello-world
    # image: registry.cn-hangzhou.aliyuncs.com/kae/hello:0.1.1
    imagePullPolicy: Always
    # args: ["xx", "xx"]
    command: ['hello-world']
    env:                     # environments
      - ENVA=a
    tty: false               # whether allocate tty
    # workingDir: xxx          # working dir

    ports:
    - containerPort: 8080
"""


def make_specs_text(appname=default_appname,
                    container_user=None,
                    builds=default_builds,
                    volumes=None,
                    base='python:latest',
                    subscribers='#platform',
                    **kwargs):
    specs_dict = locals()
    kwargs = specs_dict.pop('kwargs')
    for k, v in kwargs.items():
        specs_dict[k] = v

    specs_dict = {k: copy.deepcopy(v) for k, v in specs_dict.items()
                  if v is not None}
    specs_string = yaml.dump(specs_dict)
    return specs_string


def make_specs(appname=default_appname,
               container_user=None,
               builds=default_builds,
               volumes=['/tmp:/home/{}/tmp'.format(default_appname)],
               base='python:latest',
               subscribers='#platform',
               crontab=[],
               **kwargs):
    specs_dict = locals()
    kwargs = specs_dict.pop('kwargs')
    for k, v in kwargs.items():
        specs_dict[k] = v

    specs_dict = {k: copy.deepcopy(v) for k, v in specs_dict.items()
                  if v is not None}
    specs_string = yaml.dump(specs_dict)
    unmarshal_result = app_specs_schema.load(specs_dict)
    return unmarshal_result.data
