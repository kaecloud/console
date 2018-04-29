# -*- coding: utf-8 -*-

import json
import logging
from celery import Celery, Task
from flask import jsonify, g, session, Flask, request
from raven.contrib.flask import Sentry
from werkzeug.utils import import_string

from console.config import TASK_PUBSUB_CHANNEL, DEBUG, SENTRY_DSN, TASK_PUBSUB_EOF
from console.ext import rds, sess, db, mako, cache, sockets, init_oauth
from console.libs.datastructure import DateConverter
from console.libs.jsonutils import VersatileEncoder
from console.libs.utils import notbot_sendmsg


if DEBUG:
    loglevel = logging.DEBUG
else:
    logging.getLogger('requests').setLevel(logging.CRITICAL)
    logging.getLogger('urllib3').setLevel(logging.CRITICAL)
    loglevel = logging.INFO

logging.basicConfig(level=loglevel, format='[%(asctime)s] [%(process)d] [%(levelname)s] [%(filename)s @ %(lineno)s]: %(message)s', datefmt='%Y-%m-%d %H:%M:%S %z')

api_blueprints = [
    'app',
    'user',
]


def make_celery(app):
    celery = Celery(app.import_name)
    celery.config_from_object('console.config')

    class ContextTask(Task):

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
            msg = 'console task {}:\nargs\n```\n{}\n```\nkwargs:\n```\n{}\n```\nerror message:\n```\n{}\n```'.format(self.name, args, kwargs, str(exc))
            notbot_sendmsg('#platform', msg)

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return super(ContextTask, self).__call__(*args, **kwargs)

    celery.Task = ContextTask
    celery.autodiscover_tasks(['console'])
    return celery


def create_app():
    app = Flask(__name__)
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

    if not DEBUG:
        sentry = Sentry(dsn=SENTRY_DSN)
        sentry.init_app(app)

    for bp_name in api_blueprints:
        bp = import_string('%s.api.%s:bp' % (__package__, bp_name))
        app.register_blueprint(bp)

    # action APIs are all websockets
    from console.api.action import ws
    sockets.register_blueprint(ws)

    @app.before_request
    def init_global_vars():
        g.start = request.args.get('start', type=int, default=0)
        g.limit = request.args.get('limit', type=int, default=20)

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

    @app.route('/')
    def hello_world():
        return 'Hello world'

    return app


app = create_app()
celery = make_celery(app)
