import json
from flask import session, g
from json.decoder import JSONDecodeError
from marshmallow import ValidationError

from console.libs.utils import logger, make_app_watcher_channel_name, make_errmsg
from console.libs.jsonutils import VersatileEncoder
from console.libs.k8s import kube_api, ApiException
from console.libs.validation import build_args_schema, cluster_args_schema, cluster_canary_schema
from console.libs.view import create_api_blueprint, user_require
from console.models import App, Job
from console.tasks import celery_task_stream_response, build_image
from console.ext import rds

ws = create_api_blueprint('ws', __name__, url_prefix='ws', jsonize=False, handle_http_error=False)


@ws.route('/app/<appname>/pods/events')
@user_require(False)
def get_app_pods_events(socket, appname):
    payload = None
    while True:
        message = socket.receive()
        try:
            payload = cluster_canary_schema.loads(message)
            break
        except ValidationError as e:
            socket.send(json.dumps(e.messages))
        except JSONDecodeError as e:
            socket.send(json.dumps({'error': str(e)}))
        except Exception as e:
            logger.exception("Failed to receive payload")
            socket.send(json.dumps({'error': 'internal error, pls contact administrator'}))

    args = payload.data
    cluster = args['cluster']
    canary = args['canary']
    name = "{}-canary".format(appname) if canary else appname
    channel = make_app_watcher_channel_name(cluster, name)

    pod_list = kube_api.get_app_pods(name, cluster_name=cluster)
    pods = pod_list.to_dict()
    for item in pods['items']:
        data = {
            'object': item,
            'action': "ADDED",
        }
        socket.send(json.dumps(data, cls=VersatileEncoder))

    pubsub = rds.pubsub()
    pubsub.subscribe(channel)
    for item in pubsub.listen():
        if item['type'] == 'message':
            raw_content = item['data']
            # omit the initial message where item['data'] is 1L
            if not isinstance(raw_content, (bytes, str)):
                continue
            content = raw_content
            if isinstance(content, bytes):
                content = content.decode('utf-8')
            socket.send(content)


@ws.route('/app/<appname>/build')
@user_require(False)
def build_app(socket, appname):
    """Build an image for the specified release.
    ---
    definitions:
      BuildArgs:
        type: object
        properties:
          tag:
            type: object

    parameters:
      - name: appname
        in: path
        type: string
        required: true
      - name: build_args
        in: body
        required: true
        schema:
          $ref: '#/definitions/BuildArgs'
    responses:
      200:
        description: multiple stream messages
        schema:
          $ref: '#/definitions/StreamMessage'
      400:
        description: Error information
        schema:
          $ref: '#/definitions/Error'
        examples:
          error: "xxx"
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
        except Exception as e:
            logger.exception("Failed to receive build args payload")
            socket.send(json.dumps({'error': 'internal error, pls contact administrator'}))

    args = payload.data
    tag = args["tag"]

    app = App.get_by_name(appname)
    if not app:
        socket.send(make_errmsg('app {} not found'.format(appname), jsonize=True))
        return

    if not g.user.granted_to_app(app):
        socket.send(make_errmsg('You\'re not granted to this app, ask administrators for permission', jsonize=True))
        return
    release = app.get_release_by_tag(tag)
    if not release:
        socket.send(make_errmsg('release {} not found.'.format(tag), jsonize=True))
        return

    async_result = build_image.delay(appname, tag)
    for m in celery_task_stream_response(async_result.task_id):
        socket.send(m)


@ws.route('/job/<jobname>/log/events')
@user_require(False)
def get_job_log_events(socket, jobname):
    """
    SSE endpoint fo job log
    ---
    responses:
      200:
        description: event stream
        schema:
          type: object
    """
    job = Job.get_by_name(jobname)
    if not job:
        socket.send(json.dumps({"error": "job {} not found".format(jobname)}))
        return
    try:
        pods = kube_api.get_job_pods(jobname)
        if pods.items:
            podname = pods.items[0].metadata.name
            for line in kube_api.follow_pod_log(podname=podname):
                socket.send(json.dumps({'data': line}))
        else:
            socket.send(json.dumps({"error": "no log, please retry"}))
    except ApiException as e:
        socket.send(json.dumps({"error": "Error when create job: {}".format(str(e))}))
    except Exception as e:
        logger.exception("error when get job({}) or job logs".format(jobname))
        socket.send(json.dumps({"error": "internal error when get job log, please contact administrator"}))
