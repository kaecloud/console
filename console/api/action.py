# -*- coding: utf-8 -*-
'''
Action APIs using websocket, upon connection, client should send a first json
payload, and then server will work and steam the output as websocket frames
'''

import json
from flask import session
from json.decoder import JSONDecodeError
from marshmallow import ValidationError

from console.libs.utils import logger
from console.libs.validation import renew_schema, build_args_schema, deploy_schema, remove_container_schema, deploy_elb_schema
from console.libs.view import create_api_blueprint, user_require
from console.models.app import App
from console.tasks import celery_task_stream_response, build_image


ws = create_api_blueprint('action', __name__, url_prefix='action', jsonize=False, handle_http_error=False)


@ws.route('/build')
@user_require(False)
def build(socket):
    """Build an image for the specified release, the API will return all docker
    build messages, key frames as shown in the example responses

    :<json string appname: required, the app name
    :<json string sha: required, minimum length is 7

    **Example response**:

    .. sourcecode:: http

        HTTP/1.1 200 OK
        Content-Type: application/json

        {
            "id": "",
            "status": "",
            "progress": "",
            "error": "",
            "stream": "Step 1/7 : FROM python:latest as make-artifacts",
            "error_detail": {
                "code": 0,
                "message": "",
                "__class__": "ErrorDetail"
            },
            "__class__": "BuildImageMessage"
        }

        {
        "id": "0179a75e26fe",
        "status": "Pushing",
        "progress": "[==================================================>]  6.656kB",
        "error": "",
        "stream": "",
        "error_detail": {
            "code": 0,
            "message": "",
            "__class__": "ErrorDetail"
        },
        "__class__": "BuildImageMessage"
        }

        {
        "id": "",
        "status": "finished",
        "progress": "hub.ricebook.net/projecteru2/test-app:3641aca",
        "error": "",
        "stream": "finished hub.ricebook.net/projecteru2/test-app:3641aca",
        "error_detail": {
            "code": 0,
            "message": "",
            "__class__": "ErrorDetail"
        },
        "__class__": "BuildImageMessage"
        }

    """
    payload = None
    while True:
        message = socket.receive()
        try:
            payload = build_args_schema.loads(message)
            break
        except ValidationError as e:
            socket.send(json.dumps(e.messages))
        except JSONDecodeError as e:
            socket.send(json.dumps({'error': str(e)}))

    args = payload.data
    async_result = build_image.delay(args['appname'], args['sha'])
    for m in celery_task_stream_response(async_result.task_id):
        logger.debug(m)
        socket.send(m)


@ws.route('/deploy')
@user_require(False)
def deploy(socket):
    """Create containers for the specified release

    :<json string appname: required
    :<json string zone: required
    :<json string sha: required, minimum length is 7
    :<json string combo_name: required, specify the combo to use, you can
    update combo value using this API, so all parameters in the
    :http:post:`/api/app/(appname)/combo` are supported

    **Example response**:

    .. sourcecode:: http

        HTTP/1.1 200 OK
        Content-Type: application/json

        {
            "podname": "eru",
            "nodename": "c1-eru-2.ricebook.link",
            "id": "9c91d06cb165e829e8e0ad5d5b5484c47d4596af04122489e4ead677113cccb4",
            "name": "test-app_web_kMqYFQ",
            "error": "",
            "success": true,
            "cpu": {"0": 20},
            "quota": 0.2,
            "memory": 134217728,
            "publish": {"bridge": "172.17.0.5:6789"},
            "hook": "I am the hook output",
            "__class__": "CreateContainerMessage"
        }

    """
    payload = None
    while True:
        message = socket.receive()
        try:
            payload = deploy_schema.loads(message)
            break
        except ValidationError as e:
            socket.send(json.dumps(e.messages))
        except JSONDecodeError as e:
            socket.send(json.dumps({'error': str(e)}))

    args = payload.data
    appname = args['appname']
    app = App.get_by_name(appname)
    if not app:
        socket.send(json.dumps({'error': 'app {} not found'.format(appname)}))
        socket.close()

    combo_name = args['combo_name']
    combo = app.get_combo(combo_name)
    if not combo:
        socket.send(json.dumps({'error': 'combo {} for app {} not found'.format(combo_name, app)}))
        socket.close()

    combo.update(**{k: v for k, v in args.items() if hasattr(combo, k)})

    for m in celery_task_stream_response(async_result.task_id):
        logger.debug(m)
        socket.send(m)


