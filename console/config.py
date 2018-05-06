# -*- coding: utf-8 -*-

import os
import pathlib
import shutil
import redis
from datetime import timedelta
from smart_getenv import getenv

DEBUG = getenv('DEBUG', default=False, type=bool)
FAKE_USER = {
    'id': 12345,
    'username': 'sheldon',
    'nickname': 'Sheldon Lee Cooper',
    'email': 'sheldon@sheldon.com',
    'privileged': 1,
}

PROJECT_NAME = LOGGER_NAME = 'console'
CONFIG_DIR = getenv('CONFIG_DIR', default='/etc/kae-console')
K8S_SECRETS_DIR = os.path.join(CONFIG_DIR, "k8s")
CONTAINER_SECRETS_DIR = os.path.join(CONFIG_DIR, ".secrets__")

CONSOLE_CONFIG_PATH = getenv('CONSOLE_CONFIG_PATH',
                             default=[
                                 'console/local_config.py',
                                 os.path.join(K8S_SECRETS_DIR, 'config.py')])
# SERVER_NAME = getenv('SERVER_NAME', default='127.0.0.1')
SENTRY_DSN = getenv('SENTRY_DSN', default='')
SECRET_KEY = getenv('SECRET_KEY', default='testsecretkey')

REDIS_URL = getenv('REDIS_URL', default='redis://127.0.0.1:6379/0')

USE_KUBECONFIG = bool(getenv("USE_KUBECONFIG", default=False))

DEFAULT_NS = getenv('DEFAULT_NS', default='kae')
BUILD_NS = getenv('BUILD_NS', default='kae')

SQLALCHEMY_DATABASE_URI = getenv('SQLALCHEMY_DATABASE_URI')
SQLALCHEMY_TRACK_MODIFICATIONS = getenv('SQLALCHEMY_TRACK_MODIFICATIONS', default=True, type=bool)
# you should set SQLALCHEMY_POOL_RECYCLE to a value smaller than wait_timeout config in mysql
SQLALCHEMY_POOL_RECYCLE = getenv('SQLALCHEMY_POOL_RECYCLE', default=580)

OAUTH_APP_NAME = 'gitlab'
# I registered a test app on gitlab that redirect to
# http://console.gtapp.xyz/user/authorized as callback url
GITLAB_CLIENT_ID = getenv('GITLAB_CLIENT_ID')
GITLAB_CLIENT_SECRET = getenv('GITLAB_CLIENT_SECRET')
GITLAB_HOST = getenv('GITLAB_HOST', default='gitlab.com')

EMAIL_DOMAIN = getenv('EMAIL_DOMAIN')
BOT_WEBHOOK_URL = getenv('BOT_WEBHOOK_URL')

BASE_DOMAIN = getenv('BASE_DOMAIN')

DEFAULT_REGISTRY = "registry.cn-hangzhou.aliyuncs.com/kae"
REGISTRY_AUTHS = {
    "registry.cn-hangzhou.aliyuncs.com": "aliyun",
}

HOST_DATA_DIR = "/data/kae"
POD_LOG_DIR = "/kae/logs"

if isinstance(CONSOLE_CONFIG_PATH, str):
    CONSOLE_CONFIG_PATH = [CONSOLE_CONFIG_PATH]

for path in CONSOLE_CONFIG_PATH:
    if not os.path.isfile(path):
        continue
    exec(open(path, encoding='utf-8').read())
    break

if SQLALCHEMY_DATABASE_URI is None:
    raise ValueError("SQLALCHEMY_DATABASE_URI can't be None")

if REDIS_URL is None:
    raise ValueError("REDIS_URL can't be None")

if BASE_DOMAIN is None:
    raise ValueError("BASE_DOMAIN can't be None")

##################################################
# the config below must not use getenv
##################################################

# flask-session settings
SESSION_USE_SIGNER = True
SESSION_TYPE = 'redis'
SESSION_REDIS = redis.Redis.from_url(REDIS_URL)
SESSION_KEY_PREFIX = '{}:session:'.format(PROJECT_NAME)
PERMANENT_SESSION_LIFETIME = timedelta(days=5)

# flask cache settings
CACHE_REDIS_URL = REDIS_URL

HOST_VOLUMES_DIR = os.path.join(HOST_DATA_DIR, "volumes")
REPO_DATA_DIR = "/tmp/repo-data"

if not os.path.exists(REPO_DATA_DIR):
    os.makedirs(REPO_DATA_DIR)


# prepare for git command
def setup_git_ssh(setup_known_hosts=False):
    pathlib.Path(CONTAINER_SECRETS_DIR).mkdir(parents=True, exist_ok=True)

    src_secret = os.path.join(K8S_SECRETS_DIR, "id_rsa")
    src_known_hosts = os.path.join(K8S_SECRETS_DIR, "known_hosts")

    secret = os.path.join(CONTAINER_SECRETS_DIR, "id_rsa")
    known_hosts = os.path.join(CONTAINER_SECRETS_DIR, "known_hosts")

    shutil.copyfile(src_secret, secret)
    os.chmod(secret, 0o600)

    if setup_known_hosts:
        shutil.copyfile(src_known_hosts, known_hosts)
        ssh_cmd = "ssh -q -o UserKnownHostsFile={} -i {}".format(known_hosts, secret)
    else:
        ssh_cmd = "ssh -q -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -i {}".format(secret)
    os.environ['GIT_SSH_COMMAND'] = ssh_cmd


def setup_docker_config_json():
    pathlib.Path(CONTAINER_SECRETS_DIR).mkdir(parents=True, exist_ok=True)

    os.environ['DOCKER_CONFIG'] = CONTAINER_SECRETS_DIR
    src_docker_cfg = os.path.join(K8S_SECRETS_DIR, 'docker_config.json')
    dst_docker_cfg = os.path.join(CONTAINER_SECRETS_DIR, 'config.json')
    shutil.copyfile(src_docker_cfg, dst_docker_cfg)


setup_git_ssh()
setup_docker_config_json()
