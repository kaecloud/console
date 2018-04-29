# -*- coding: utf-8 -*-

import os
import redis
from datetime import timedelta
from kombu import Queue
from smart_getenv import getenv


DEBUG = getenv('DEBUG', default=False, type=bool)
FAKE_USER = {
    'id': 12345,
    'name': 'timfeirg',
    'email': 'timfeirg@ricebook.com',
    'access_token': 'faketoken',
    'privileged': 1,
}

PROJECT_NAME = LOGGER_NAME = 'console'
CONSOLE_CONFIG_PATH = getenv('CONSOLE_CONFIG_PATH', default=['console/local_config.py', '/etc/console/console.py'])
SERVER_NAME = getenv('SERVER_NAME', default='console.test.ricebook.net')
SENTRY_DSN = getenv('SENTRY_DSN', default='')
SECRET_KEY = getenv('SECRET_KEY', default='testsecretkey')

REDIS_URL = getenv('REDIS_URL', default='redis://127.0.0.1:6379/0')

DEFAULT_NS = getenv('DEFAULT_NS', default='kae')
BUILD_NS = getenv('BUILD_NS', default='kae')

SQLALCHEMY_DATABASE_URI = getenv('SQLALCHEMY_DATABASE_URI', default='mysql+pymysql://root:123qwe@localhost:3306/consoletest?charset=utf8mb4')
SQLALCHEMY_TRACK_MODIFICATIONS = getenv('SQLALCHEMY_TRACK_MODIFICATIONS', default=True, type=bool)

OAUTH_APP_NAME = 'github'
# I registered a test app on github that redirect to
# http://console.test.ricebook.net/user/authorized as callback url
GITHUB_CLIENT_ID = getenv('GITHUB_CLIENT_ID', default='shush')
GITHUB_CLIENT_SECRET = getenv('GITHUB_CLIENT_SECRET', default='shush')
GITHUB_CLIENT_KWARGS = {'scope': 'user:email'}
# AUTHLIB not support cache any more
# OAUTH_CLIENT_CACHE_TYPE = 'redis'

CONSOLE_HEALTH_CHECK_STATS_KEY = 'console:health'

REDIS_POD_NAME = getenv('REDIS_POD_NAME', default='redis')

NOTBOT_SENDMSG_URL = getenv('NOTBOT_SENDMSG_URL')

TASK_PUBSUB_CHANNEL = 'console:task:{task_id}:pubsub'
# send this to mark EOF of stream message
# TODO: ugly
TASK_PUBSUB_EOF = 'CELERY_TASK_DONE:{task_id}'

# celery config
timezone = getenv('TIMEZONE', default='Asia/Shanghai')
broker_url = getenv('CELERY_BROKER_URL', default='redis://127.0.0.1:6379/0')
result_backend = getenv('CELERY_RESULT_BACKEND', default='redis://127.0.0.1:6379/0')
broker_transport_options = {'visibility_timeout': 10}
task_default_queue = PROJECT_NAME
task_queues = (
    Queue(PROJECT_NAME, routing_key=PROJECT_NAME),
)
task_default_exchange = PROJECT_NAME
task_default_routing_key = PROJECT_NAME
task_serializer = 'json'
result_serializer = 'json'
accept_content = ['json', 'pickle']

if isinstance(CONSOLE_CONFIG_PATH, str):
    CONSOLE_CONFIG_PATH = [CONSOLE_CONFIG_PATH]

for path in CONSOLE_CONFIG_PATH:
    if not os.path.isfile(path):
        continue
    print('load from {}'.format(path))
    exec(open(path, encoding='utf-8').read())
    break

# flask-session settings
SESSION_USE_SIGNER = True
SESSION_TYPE = 'redis'
SESSION_REDIS = redis.Redis.from_url(REDIS_URL)
SESSION_KEY_PREFIX = '{}:session:'.format(PROJECT_NAME)
PERMANENT_SESSION_LIFETIME = timedelta(days=5)

# flask cache settings
CACHE_REDIS_URL = REDIS_URL

