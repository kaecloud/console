# -*- coding: utf-8 -*-

import copy
import random
import string
import yaml

from console.libs.specs import app_specs_schema


# core_online = False
# try:
#     for zone in ZONE_CONFIG.values():
#         ip, port = zone['CORE_URL'].split(':')
#         Telnet(ip, port).close()
#         core_online = True
# except ConnectionRefusedError:
#     core_online = False


def fake_sha(length):
    return ''.join(random.choice(string.hexdigits.lower()) for _ in range(length))


default_appname = 'test-app'
default_sha = fake_sha(40)

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
git: yangyu0.github.com
type: web

service:
  user: root
  replicas: 2
  labels:
    - proctype=router

  mountpoints:
    - a.external.domain1/b/c
  ports:
  - port: 80
    targetPort: 8080

  containers:
  - name: hello-world
    image: yuyang0/hello-world
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
