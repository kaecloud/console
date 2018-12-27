import time
import json
import html
from functools import wraps
import contextlib
from flask import session, g, current_app, request, url_for
from json.decoder import JSONDecodeError
from marshmallow import ValidationError
import gevent
from geventwebsocket.exceptions import WebSocketError
from urllib3.exceptions import ProtocolError
import redis_lock

from console.libs.utils import (
    logger, make_app_watcher_channel_name, make_errmsg, send_email,
    build_image_helper, BuildError
)
from console.libs.jsonutils import VersatileEncoder
from console.libs.k8s import kube_api, ApiException
from console.libs.validation import (
    build_args_schema, cluster_args_schema, cluster_canary_schema, pod_entry_schema
)
from console.libs.view import create_api_blueprint, user_require
from console.models import App, Job, User, get_current_user
from console.tasks import celery_task_stream_response, build_image
from console.ext import rds, db
from console.config import DEFAULT_APP_NS, DEFAULT_JOB_NS, NGINX_READ_TIMEOUT, FAKE_USER

ws = create_api_blueprint('ws', __name__, url_prefix='ws', jsonize=False, handle_http_error=False)


def ws_user_require(privileged=False):
    def _user_require(func):
        @wraps(func)
        def _(socket, *args, **kwargs):
            if current_app.config['DEBUG']:
                g.user = User(**FAKE_USER)
            else:
                g.user = get_current_user()
            if not g.user:
                # TODO: change the message
                socket.send(make_errmsg('please login, login url: {}?next={}'.format(url_for('user.login'), request.url), jsonize=True))
                socket.close()
                return
            elif privileged and g.user.privileged != 1:
                socket.send(make_errmsg('dude you are not administrator', jsonize=True))
                socket.close()
                return

            return func(socket, *args, **kwargs)
        return _
    return _user_require


@contextlib.contextmanager
def session_removed():
    db.session.remove()
    yield


def ignore_socket_dead(f):
    @wraps(f)
    def _inner(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except WebSocketError as e:
            logger.warn("send failed: {}".format(str(e)))
    return _inner


@ws.route('/app/<appname>/pods/events')
@ignore_socket_dead
@ws_user_require(False)
def get_app_pods_events(socket, appname):
    payload = None
    socket_active_ts = time.time()

    while True:
        message = socket.receive()
        if message is None:
            return
        try:
            payload = cluster_canary_schema.loads(message)
            break
        except ValidationError as e:
            socket.send(json.dumps(e.messages))
        except JSONDecodeError as e:
            socket.send(json.dumps({'error': str(e)}))

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
            while need_exit is False:
                if socket.receive() is None:
                    need_exit = True
                    break

        def heartbeat_sender():
            nonlocal need_exit, socket_active_ts
            interval = NGINX_READ_TIMEOUT - 3
            if interval <= 0:
                interval = NGINX_READ_TIMEOUT

            while need_exit is False:
                now = time.time()
                if now - socket_active_ts <= (interval-1):
                    time.sleep(interval - (now - socket_active_ts))
                else:
                    try:
                        socket.send("PONG")
                        socket_active_ts = time.time()
                    except WebSocketError as e:
                        need_exit = True
                        return

        gevent.spawn(check_client_socket)
        gevent.spawn(heartbeat_sender)

        try:

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
                    socket.send(content)
                    socket_active_ts = time.time()
        finally:
            # need close the connection created by PUB/SUB,
            # otherwise it will cause too many redis connections
            pubsub.unsubscribe()
            pubsub.close()
            need_exit = True
    logger.info("ws connection closed")


@ws.route('/app/<appname>/build')
@ignore_socket_dead
@ws_user_require(False)
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
    total_msg = []
    client_closed = False

    phase = None
    def handle_msg(ss):
        nonlocal phase
        try:
            m = json.loads(ss)
        except:
            return False
        if m['success'] is False:
            total_msg.append(m['error'])
            return False
        if phase != m['phase']:
            phase = m['phase']
            total_msg.append("***** PHASE {}".format(m['phase']))

        raw_data = m.get('raw_data', None)
        if raw_data is None:
            raw_data = {}
        if raw_data.get('error', None):
            total_msg.append((str(raw_data)))
            return False

        if phase.lower() == "pushing":
            if len(raw_data) == 1 and 'status' in raw_data:
                total_msg.append(raw_data['status'])
            elif 'id' in raw_data and 'status' in raw_data:
                # TODO: make the output like docker push
                total_msg.append("{}:{}".format(raw_data['id'], raw_data['status']))
            elif 'digest' in raw_data:
                total_msg.append("{}: digest: {} size: {}".format(raw_data.get('status'), raw_data['digest'], raw_data.get('size')))
            else:
                total_msg.append(str(m))
        else:
            total_msg.append(m['msg'])
        return True

    while True:
        message = socket.receive()
        if message is None:
            return
        try:
            payload = build_args_schema.loads(message)
            break
        except ValidationError as e:
            socket.send(json.dumps(e.messages))
        except JSONDecodeError as e:
            socket.send(json.dumps({'error': str(e)}))

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

    def heartbeat_sender():
        nonlocal client_closed
        interval = NGINX_READ_TIMEOUT - 3
        if interval <= 0:
            interval = NGINX_READ_TIMEOUT

        while client_closed is False:
            try:
                time.sleep(interval)
                socket.send("PONG")
            except WebSocketError as e:
                client_closed = True
                return

    gevent.spawn(heartbeat_sender)

    # don't allow multiple build tasks for single app
    lock_name = "__app_lock_{}_build_aaa".format(appname)
    lck = redis_lock.Lock(rds, lock_name, expire=30, auto_renewal=True)
    if lck.acquire(blocking=block):
        try:
            async_result = build_image.delay(appname, tag)
            for m in celery_task_stream_response(async_result.task_id, 900):
                # after 10 minutes, we still can't get output message, so we exit the build task
                try:
                    if m is None:
                        async_result.revoke(terminate=True)
                        socket.send(make_errmsg("doesn't receive any messages in last 15 minutes, build task for app {} seems to be stuck".format(appname), jsonize=True))
                        break
                    if handle_msg(m) is False:
                        break
                    if client_closed is False:
                        socket.send(m)
                except WebSocketError as e:
                    # when client is disconnected, we shutdown the build task
                    # TODO: maybe need to wait task to exit.
                    client_closed = True
                    logger.warn("Can't send build msg to client: {}".format(str(e)))
        except Exception as e:
            socket.send(make_errmsg("error when build app {}: {}".format(appname, str(e)), jsonize=True))
        finally:
            lck.release()
            # if client websocket is closed, we send an email to the user
            if phase.lower() != "finished":
                subject = "KAE: Failed to build {}:{}".format(appname, tag)
                text_title = '<h2 style="color: #ff6161;"> Build Failed </h2>'
                build_result_text = '<strong style="color:#ff6161;"> build terminates prematurely.</strong>'
            else:
                subject = 'KAE: build {}:{} successfully'.format(appname, tag)
                text_title = '<h2 style="color: #00d600;"> Build Success </h2>'
                build_result_text = '<strong style="color:#00d600; font-weight: 600">Build %s %s done.</strong>' % (appname, tag)
            email_text_tpl = '''<div>
  <div>{}</div>
  <div style="background:#000; padding: 15px; color: #c4c4c4;">
    <pre>{}</pre>
  </div>
</div>'''
            email_text = email_text_tpl.format(text_title, html.escape("\n".join(total_msg)) + '\n' + build_result_text)
            email_list = [u.email for u in app.users]
            send_email(email_list, subject, email_text)
    else:
        socket.send(make_errmsg("there seems exist another build task and you set block to {}".format(block), jsonize=True))


@ws.route('/job/<jobname>/log/events')
@ignore_socket_dead
@ws_user_require(False)
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
    with session_removed():
        try:
            pods = kube_api.get_job_pods(jobname, namespace=ns)
        except ApiException as e:
            socket.send(json.dumps({"error": "Error when get job pods: {}".format(str(e))}))
            return
        if pods.items:
            podname = pods.items[0].metadata.name
            try:
                for line in kube_api.follow_pod_log(podname=podname, namespace=ns):
                    socket.send(json.dumps({'data': line}))
            except ApiException as e:
                socket.send(json.dumps({"error": "Error when follow job log, please retry: {}".format(str(e))}))
                return
        else:
            socket.send(json.dumps({"error": "no log, please retry"}))


@ws.route('/app/<appname>/entry')
@ignore_socket_dead
@ws_user_require(False)
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

    def heartbeat_sender():
        nonlocal need_exit
        interval = NGINX_READ_TIMEOUT - 3
        if interval <= 0:
            interval = NGINX_READ_TIMEOUT

        while need_exit is False:
            time.sleep(interval)
            try:
                # send a null character to client
                socket.send('\0')
            except WebSocketError as e:
                need_exit = True
                return

    def resp_sender():
        nonlocal need_exit
        try:
            while sh.is_open() and need_exit is False:
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
            logger.warn('kubernetes disconnect client after default 10m...')
        except WebSocketError as e:
            logger.warn('client socket is closed')
        finally:
            need_exit = True
            logger.info("exec output sender greenlet exit")

    gevent.spawn(resp_sender)
    gevent.spawn(heartbeat_sender)
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
