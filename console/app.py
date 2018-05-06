# -*- coding: utf-8 -*-

import yaml
import logging
from flask import jsonify, g, Flask, request
from flasgger import Swagger

from raven.contrib.flask import Sentry
from werkzeug.utils import import_string

from console.config import DEBUG, SENTRY_DSN
from console.ext import sess, db, mako, cache, init_oauth
from console.libs.datastructure import DateConverter

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
    'user',
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
    name: X-Access-Token,
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


def create_app():
    app = Flask(__name__)
    app.config['SWAGGER'] = {
        'title': 'KAE Console API',
        'uiversion': 3,
    }
    swagger = Swagger(app, template=yaml.load(swagger_yaml_template))

    app.url_map.converters['date'] = DateConverter
    app.config.from_object('console.config')
    app.secret_key = app.config['SECRET_KEY']

    app.url_map.strict_slashes = False

    db.init_app(app)
    init_oauth(app)
    mako.init_app(app)
    cache.init_app(app)
    sess.init_app(app)

    if not DEBUG:
        sentry = Sentry(dsn=SENTRY_DSN)
        sentry.init_app(app)

    for bp_name in api_blueprints:
        bp = import_string('%s.api.%s:bp' % (__package__, bp_name))
        app.register_blueprint(bp)

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

    @app.route('/healthz')
    def healthz():
        """
        health status
        ---
        security: []
        responses:
          200:
            description: Health status
            examples:
              text/plain:
                "ok"
        """
        return 'ok'

    return app


app = create_app()
