# -*- coding: utf-8 -*-

import yaml
import json
import logging

from raven.contrib.flask import Sentry
from celery import Celery, Task
from flask import jsonify, g, Flask, request
# from flask_cors import CORS
from flask_migrate import Migrate
from flask_admin import Admin
from flasgger import Swagger

from werkzeug.utils import import_string

from console.config import (
    DEBUG, SENTRY_DSN, STATIC_DIR, TEMPLATE_DIR, TASK_PUBSUB_CHANNEL,
    TASK_PUBSUB_EOF,
)
from console.ext import sess, db, mako, cache, init_oauth, rds, sockets
from console.libs.datastructure import DateConverter
from console.libs.jsonutils import VersatileEncoder
from console.libs.utils import bearychat_sendmsg


if DEBUG:
    loglevel = logging.DEBUG
else:
    logging.getLogger('requests').setLevel(logging.CRITICAL)
    logging.getLogger('urllib3').setLevel(logging.CRITICAL)
    loglevel = logging.INFO

logging.basicConfig(level=loglevel,
                    format='[%(asctime)s] [%(process)d] [%(levelname)s] [%(filename)s @ %(lineno)s]: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S %z')

api_blueprints = [
    'app',
    'job',
    'user',
    'home',
    'cluster',
]

swagger_yaml_template = """
swagger: 2.0
info:
  title: KAE Console API
  description: API definition for KAE Console
  contact:
    email: yyangplus@NOSPAM.gmail.com
    url: https://github.com/kaecloud
  termsOfService: http://me.com/terms
  version: 0.0.1
schemes:
  - http
  - https
securityDefinitions:
  api_key:
    type: apiKey
    name: X-Private-Token,
    in: header
security:
  - api_key: []


definitions:
  App:
    type: object
    properties:
      id:
        type: integer
      created:
        type: string
      updated:
        type: string
      name:
        type: string
      git:
        type: string
      type:
        type: string
  Cluster:
    type: object
    properties:
      id:
        type: integer
      created:
        type: string
      updated:
        type: string
      name:
        type: string
  Job:
    type: object
    properties:
      id:
        type: integer
      created:
        type: string
      updated:
        type: string
      name:
        type: string
      git:
        type: string
      branch:
        type: string
      commit:
        type: string
      specs_text:
        type: string
  Error:
    type: object
    properties:
      error:
        type: string
  User:
    type: object
    properties:
      id:
        type: integer
      created:
        type: string
      updated:
        type: string
      username:
        type: string
      nickname:
        type: string
      email:
        type: string
      avatar:
        type: string
      privileged:
        type: boolean
      data:
        type: string
  Release:
    type: object
    properties:
      id:
        type: integer
      app_id:
        type: integer
      image:
        type: string
      specs_text:
        type: string
      misc:
        type: string
      build_status:
        type: boolean
      created:
        type: string
      updated:
        type: string
  OPLog:
    type: object
    properties:
      id:
        type: integer
      appname:
        type: string
      action:
        type: string
      tag:
        type: string
      content:
        type: string
      username:
        type: string
      created:
        type: string
      updated:
        type: string
  StreamMessage:
    type: object
    properties:
      phase:
        type: string
      success:
        type: boolean
      error:
        type: string
      msg:
        type: string
      raw_data:
        type: object
      progress:
        type: string
"""


def make_celery(app):
    celery = Celery(app.import_name)
    celery.config_from_object('console.config')

    class KAETask(Task):

        abstract = True

        def stream_output(self, data, task_id=None):
            channel_name = TASK_PUBSUB_CHANNEL.format(task_id=task_id or self.request.id)
            rds.publish(channel_name, json.dumps(data, cls=VersatileEncoder))

        def on_success(self, retval, task_id, args, kwargs):
            channel_name = TASK_PUBSUB_CHANNEL.format(task_id=task_id)
            rds.publish(channel_name, TASK_PUBSUB_EOF.format(task_id=task_id))

        def on_failure(self, exc, task_id, args, kwargs, einfo):
            channel_name = TASK_PUBSUB_CHANNEL.format(task_id=task_id)
            failure_msg = {'error': str(exc), 'args': args, 'kwargs': kwargs}
            rds.publish(channel_name, json.dumps(failure_msg, cls=VersatileEncoder))
            rds.publish(channel_name, TASK_PUBSUB_EOF.format(task_id=task_id))
            msg = 'Console task {}:\nargs\n```\n{}\n```\nkwargs:\n```\n{}\n```\nerror message:\n```\n{}\n```'.format(self.name, args, kwargs, str(exc))
            bearychat_sendmsg('platform', msg)

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return super(KAETask, self).__call__(*args, **kwargs)

    celery.Task = KAETask
    celery.autodiscover_tasks(['console'])
    return celery


def create_app():
    app = Flask(__name__)

    # CORS(app)
    # cors = CORS(app, resources={r"/api/*": {"origins": "*"}})

    app.config['SWAGGER'] = {
        'title': 'KAE Console API',
        'uiversion': 3,
    }

    app.url_map.converters['date'] = DateConverter
    app.config.from_object('console.config')
    app.secret_key = app.config['SECRET_KEY']

    app.url_map.strict_slashes = False

    make_celery(app)
    db.init_app(app)
    init_oauth(app)
    mako.init_app(app)
    cache.init_app(app)
    sess.init_app(app)
    sockets.init_app(app)

    migrate = Migrate(app, db)

    from console.admin import init_admin
    admin = Admin(app, name='KAE', template_mode='bootstrap3')
    init_admin(admin)

    from console.libs.view import user_require
    swagger = Swagger(app, decorators=[user_require(False), ], template=yaml.load(swagger_yaml_template))

    if not DEBUG:
        sentry = Sentry(dsn=SENTRY_DSN)
        sentry.init_app(app)

    for bp_name in api_blueprints:
        bp = import_string('%s.api.%s:bp' % (__package__, bp_name))
        app.register_blueprint(bp)

    from console.api.ws import ws
    sockets.register_blueprint(ws)

    @app.before_request
    def init_global_vars():
        g.start = request.args.get('start', type=int, default=0)
        g.limit = request.args.get('limit', type=int, default=20)

    @app.after_request
    def apply_caching(response):
        # TODO: remove the code
        response.headers['Access-Control-Allow-Origin'] = 'http://127.0.0.1:9090'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Origin, X-Requested-With, Content-Type, Accept, Connection, User-Agent, Cookie'
        response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
        return response

    @app.errorhandler(422)
    def handle_unprocessable_entity(err):
        # webargs attaches additional metadata to the `data` attribute
        exc = getattr(err, 'exc')
        if exc:
            # Get validations from the ValidationError object
            messages = exc.messages
        else:
            messages = ['Invalid request']
        return jsonify({
            'messages': messages,
        }), 422

    @app.before_first_request
    def prepare_k8s():
        # placeholder to prepare environment
        pass

    return app


app = create_app()
celery = make_celery(app)
