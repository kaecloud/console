import json
import contextlib
from flask import session, g
from json.decoder import JSONDecodeError
from marshmallow import ValidationError
import gevent
from geventwebsocket.exceptions import WebSocketError
from urllib3.exceptions import ProtocolError
import redis_lock

from console.libs.utils import logger, make_app_watcher_channel_name, make_errmsg, build_image_helper, BuildError
from console.libs.jsonutils import VersatileEncoder
from console.libs.k8s import kube_api, ApiException
from console.libs.validation import (
    build_args_schema, cluster_args_schema, cluster_canary_schema, pod_entry_schema
)
from console.libs.view import create_api_blueprint, user_require
from console.models import App, Job
from console.tasks import celery_task_stream_response, build_image
from console.ext import rds, db
from console.config import DEFAULT_APP_NS, DEFAULT_JOB_NS

ws = create_api_blueprint('ws', __name__, url_prefix='ws', jsonize=False, handle_http_error=False)


@contextlib.contextmanager
def session_removed():
    db.session.remove()
    yield


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
    ns = DEFAULT_APP_NS

    app = App.get_by_name(appname)
    if not app:
        socket.send(make_errmsg('app {} not found'.format(appname), jsonize=True))
        return

    if not g.user.granted_to_app(app):
        socket.send(make_errmsg('You\'re not granted to this app, ask administrators for permission', jsonize=True))
        return

    # since this request may pend long time, so we remove the db session
    # otherwise we may get error like `sqlalchemy.exc.TimeoutError: QueuePool limit of size 50 overflow 10 reached, connection timed out`
    with session_removed():
        pod_list = kube_api.get_app_pods(name, cluster_name=cluster, namespace=ns)
        pods = pod_list.to_dict()
        for item in pods['items']:
            data = {
                'object': item,
                'action': "ADDED",
            }
            socket.send(json.dumps(data, cls=VersatileEncoder))

        pubsub = rds.pubsub()
        pubsub.subscribe(channel)
        need_exit = False

        def check_client_socket():
            nonlocal need_exit
            if socket.receive() is None:
                need_exit = True
        try:
            gevent.spawn(check_client_socket)

            while need_exit is False:
                resp = pubsub.get_message(timeout=30)
                if resp is None:
                    continue

                if resp['type'] == 'message':
                    raw_content = resp['data']
                    # omit the initial message where resp['data'] is 1L
                    if not isinstance(raw_content, (bytes, str)):
                        continue
                    content = raw_content
                    if isinstance(content, bytes):
                        content = content.decode('utf-8')
                    try:
                        socket.send(content)
                    except WebSocketError as e:
                        logger.warn("can't send pod event msg to client: {}".format(str(e)))
                        break
        finally:
            # need close the connection created by PUB/SUB,
            # otherwise it will cause too many redis connections
            pubsub.unsubscribe()
            pubsub.close()
            need_exit = True
    logger.info("ws connection closed")


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
    block = args['block']

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

    # don't allow multiple build tasks for single app
    lock_name = "__app_lock_{}_build_aaa".format(appname)
    lck = redis_lock.Lock(rds, lock_name, expire=30, auto_renewal=True)
    try:
        if lck.acquire(blocking=block):
            try:
                async_result = build_image.delay(appname, tag)
                for m in celery_task_stream_response(async_result.task_id, 600):
                    # after 10 minutes, we still can't get output message, so we exit the build task
                    try:
                        if m is None:
                            async_result.revoke(terminate=True)
                            socket.send(make_errmsg("doesn't receive any messages in last 10 minutes, build task for app {} seems to be stuck".format(appname), jsonize=True))
                            break
                        socket.send(m)
                    except WebSocketError as e:
                        # when client is disconnected, we shutdown the build task
                        # TODO: maybe need to wait task to exit.
                        async_result.revoke(terminate=True)
                        logger.warn("Can't send build msg to client: {}".format(str(e)))
                        break
            except Exception as e:
                socket.send(make_errmsg("error when build app {}: {}".format(appname, str(e)), jsonize=True))
            finally:
                lck.release()
        else:
            socket.send(make_errmsg("there seems exist another build task and you set block to {}".format(block), jsonize=True))
    except WebSocketError as e:
        logger.warn("can't send pod event msg to client: {}".format(str(e)))


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
    ns = DEFAULT_JOB_NS

    job = Job.get_by_name(jobname)
    if not job:
        socket.send(json.dumps({"error": "job {} not found".format(jobname)}))
        return
    try:
        with session_removed():
            pods = kube_api.get_job_pods(jobname, namespace=ns)
            if pods.items:
                podname = pods.items[0].metadata.name
                for line in kube_api.follow_pod_log(podname=podname, namespace=ns):
                    try:
                        socket.send(json.dumps({'data': line}))
                    except WebSocketError as e:
                        logger.warn("Can't send job log msg to client: {}".format(str(e)))
                        break
            else:
                socket.send(json.dumps({"error": "no log, please retry"}))
    except ApiException as e:
        socket.send(json.dumps({"error": "Error when create job: {}".format(str(e))}))
    except Exception as e:
        logger.exception("error when get job({}) or job logs".format(jobname))
        socket.send(json.dumps({"error": "internal error when get job log, please contact administrator"}))


@ws.route('/app/<appname>/entry')
@user_require(False)
def enter_pod(socket, appname):
    payload = None
    while True:
        message = socket.receive()
        if message is None:
            return
        try:
            payload = pod_entry_schema.loads(message)
            break
        except ValidationError as e:
            socket.send(json.dumps(e.messages))
        except JSONDecodeError as e:
            socket.send(json.dumps({'error': str(e)}))
        except Exception as e:
            logger.exception("Failed to receive payload")
            socket.send(json.dumps({'error': 'internal error, pls contact administrator'}))

    app = App.get_by_name(appname)
    if not app:
        socket.send(make_errmsg('app {} not found'.format(appname), jsonize=True))
        return

    if not g.user.granted_to_app(app):
        socket.send(make_errmsg('You\'re not granted to this app, ask administrators for permission', jsonize=True))
        return

    args = payload.data
    podname = args['podname']
    cluster = args['cluster']
    namespace = args['namespace']
    container = args.get('container', None)
    sh = kube_api.exec_shell(podname, namespace=namespace, cluster_name=cluster, container=container)
    need_exit = False

    def resp_sender():
        nonlocal need_exit
        try:
            while need_exit is False:
                sh.update(timeout=1)
                if sh.peek_stdout():
                    msg = sh.read_stdout()
                    logger.debug("STDOUT: %s" % msg)
                    socket.send(msg)
                if sh.peek_stderr():
                    msg = sh.read_stderr()
                    logger.debug("STDERR: %s" % msg)
                    socket.send(msg)
        except ProtocolError:
            need_exit = True
            logger.warn('kubernetes disconnect client after default 10m...')
        logger.info("exec output sender greenlet exit")
    gevent.spawn(resp_sender)
    try:
        while need_exit is False:
            # get command from client
            message = socket.receive()
            if message is None:
                logger.info("client socket closed")
                break
            sh.write_stdin(message)
            continue
    finally:
        need_exit = True

