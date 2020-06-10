# -*- coding: utf-8 -*-
import os
import re
import time
import json
import string
import random
import shutil
import logging
import urllib.request
import smtplib
import threading
from smtplib import SMTPException
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.utils import COMMASPACE, formatdate
from email.encoders import encode_base64
from subprocess import Popen, PIPE, STDOUT, run, CalledProcessError

import docker
from flask import session
from functools import wraps

from console.config import (
    BOT_WEBHOOK_URL, LOGGER_NAME, DEBUG, DEFAULT_REGISTRY,
    REPO_DATA_DIR, EMAIL_SENDER, EMAIL_SENDER_PASSWOORD,
    CLUSTER_CFG, DOCKER_HOST, IM_WEBHOOK_TOKEN,
)
from console.libs.jsonutils import VersatileEncoder


logger = logging.getLogger(LOGGER_NAME)


def spawn(target, *args, **kw):
    t = threading.Thread(target=target, name=target.__name__, args=args, kwargs=kw)
    t.daemon = True
    t.start()
    return t


def with_appcontext(f):
    @wraps(f)
    def _(*args, **kwargs):
        from console.app import create_app
        app = create_app()
        with app.app_context():
            return f(*args, **kwargs)
    return _


def handle_exception(exceptions, default=None):
    def _handle_exception(f):
        @wraps(f)
        def _(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except exceptions as e:
                logger.error('Call %s error: %s', f.__name__, e)
                if callable(default):
                    return default()
                return default
        return _
    return _handle_exception


def login_user(user):
    session['id'] = user.id
    session['name'] = user.name


def shorten_sentence(s, length=88):
    if len(s) > length:
        return s[:length]
    return s


def send_post_json_request(url, dic, headers=None):
    if headers is None:
        headers = {}
    req = urllib.request.Request(url, headers=headers, method="POST")
    req.add_header('Content-Type', 'application/json')
    data = json.dumps(dic)
    data = data.encode("utf8")
    response = urllib.request.urlopen(req, data)

    data = json.loads(response.read().decode("utf8"))
    return response.getcode(), data


def send_get_json_request(url, headers=None):
    if headers is None:
        headers = {}
    req = urllib.request.Request(url=url, headers=headers, method='GET')
    res = urllib.request.urlopen(req)
    res_body = res.read()
    data = json.loads(res_body.decode("utf8"))
    return res.getcode(), data


def send_email(receivers, subject, text, sender=EMAIL_SENDER, password=EMAIL_SENDER_PASSWOORD,
               files=None, server="smtp.exmail.qq.com"):
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = COMMASPACE.join(receivers)
    msg['Date'] = formatdate(localtime=True)
    msg.attach(MIMEText(text, 'html'))
    for fname in files or []:
        with open(fname, "rb") as fp:
            part = MIMEBase('application', "octet-stream")
            part.set_payload(fp.read())
            encode_base64(part)

            part.add_header('Content-Disposition', 'attachment; filename="%s"' % os.path.basename(fname))
            msg.attach(part)

    logger.info("sending email..")
    try:
        s = smtplib.SMTP(server)
        s.login(sender, password)
        s.sendmail(sender, receivers, msg.as_string())
        s.close()
        logger.info("Sent email successfully")
        return True
    except SMTPException as e:
        logger.warning("Error: unable to send email %s" % str(e))
        return False
    except:
        logger.exception(f'Internel error: Send email failed(server: {server}')
        return False


def im_sendmsg(to, content):
    """
    send message to IM app(currently use feishu)
    :param to:
    :param content:
    :return:
    """
    if not all([to, content, BOT_WEBHOOK_URL]):
        return
    to = to.strip(';')
    if DEBUG:
        logger.debug('Sending notbot message to %s, content: %s', to, content)
        return
    content = '[console] {}'.format(content)
    data = {
        "text": content,
        "group": to,
    }
    if IM_WEBHOOK_TOKEN:
        data['token'] = IM_WEBHOOK_TOKEN
    headers = {
        'Connection': 'close',
    }
    try:
        code, res = send_post_json_request(BOT_WEBHOOK_URL, data, headers)
        return res
    except:
        logger.exception('Send im msg failed')
        return


def make_shell_env(env_content):
    """
    >>> make_shell_env([('FOO', 'BAR')])
    'export FOO="BAR"'
    """
    return '\n'.join('export {}="{}"'.format(k, v) for k, v in env_content)


def memoize(f):
    """ Memoization decorator for a function taking one or more arguments. """
    class memodict(dict):
        def __getitem__(self, *key):
            return dict.__getitem__(self, key)

        def __missing__(self, key):
            res = f(*key)
            if res:
                self[key] = res

            return res

    return memodict().__getitem__


def make_sentence_json(message):
    msg = json.dumps({'type': 'sentence', 'message': message}, cls=VersatileEncoder)
    return msg + '\n'


def id_generator(size=6, chars=string.ascii_uppercase + string.digits, prefix=""):
    return prefix + ''.join(random.choice(chars) for _ in range(size))


def generate_unique_dirname(prefix=None):
    time_str = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
    if prefix is None:
        name = "{}_{}".format(time_str, id_generator(8))
    else:
        name = "{}_{}_{}".format(prefix, time_str, id_generator(8))
    return name


def parse_image_name(image_name):
    parts = image_name.split('/', 1)
    if len(parts) == 2 and '.' in parts[0]:
        return parts[0], parts[1]
    else:
        return None, image_name


def construct_full_image_name(name, appname):
    if name:
        registry, img_name = parse_image_name(name)
        if registry is not None:
            return name
        else:
            # use docker hub
            if '/' in name:
                return name
            else:
                return DEFAULT_REGISTRY.rstrip('/') + '/' + name
    else:
        return DEFAULT_REGISTRY.rstrip('/') + '/' + appname


def make_canary_appname(appname):
    return "{}-canary".format(appname)


def make_app_redis_key(appname):
    return "app-{}-data".format(appname)


def get_cluster_names():
    return list(CLUSTER_CFG.keys())


def cluster_exists(name):
    return name in CLUSTER_CFG


def search_tls_secret(cluster, hostname):
    cluster_info = CLUSTER_CFG[cluster]
    cluster_secret_map = cluster_info.get("tls_secrets", None)
    if cluster_secret_map is None:
        return None

    if hostname in cluster_secret_map:
        return cluster_secret_map[hostname]
    else:
        parts = hostname.split('.', 1)
        if len(parts) < 2:
            return None
        parent = parts[1]
        return cluster_secret_map.get(parent, None)


def get_dfs_host_dir(cluster):
    cluster_info = CLUSTER_CFG.get(cluster, None)
    if cluster_info is None:
        return None
    return cluster_info.get("dfs_host_dir", None)


def make_app_watcher_channel_name(cluster, appname):
    return "kae-cluster-{}-app-{}-pods-watcher".format(cluster, appname)


def make_errmsg(msg, jsonize=False):
    data = {'success': False, 'error': msg}
    if jsonize:
        return json.dumps(data)
    else:
        return data


def make_msg(phase, raw_data=None, success=True, error=None, msg=None, progress=None, jsonize=False):
    d = {
        "success": success,
        "phase": phase,
        "raw_data": raw_data,
        'progress': progress,
        'msg': msg,
        'error': error,
    }
    if jsonize:
        return json.dumps(d) + '\n'
    else:
        return d


class BuildError(Exception):
    def __init__(self, data):
        self.data = data

    def __str__(self):
        return self.data


def build_image_helper(appname, release):
    git_tag = release.tag
    specs = release.specs

    if not specs.builds:
        yield make_msg("Finished", msg="ignore empty builds")
        return
    if release.build_status:
        yield make_msg("Finished", msg="already built")
        return

    # clone code
    repo_dir = os.path.join(REPO_DATA_DIR, appname)
    shutil.rmtree(repo_dir, ignore_errors=True)
    p = Popen(['git',  'clone',  '--recursive', '--progress', release.git, repo_dir], stdout=PIPE,
              stderr=STDOUT, env=os.environ.copy())

    for line in iter(p.stdout.readline, ""):
        if not line:
            break
        if isinstance(line, bytes):
            line = line.decode('utf8')
        line = line.strip(" \n")
        yield make_msg("Cloning", msg=line)
    p.wait()
    if p.returncode:
        raise BuildError(make_msg("Cloning", success=False, error="git clone error: {}".format(p.returncode)))

    try:
        run(
            "git checkout {}".format(git_tag), shell=True,
            check=True, cwd=repo_dir, stdout=PIPE, stderr=STDOUT,
            universal_newlines=True,
        )
    except CalledProcessError as e:
        raise BuildError(make_msg("Checkout", success=False, error="checkout tag error: {}".format(str(e))))

    client = docker.APIClient(base_url=DOCKER_HOST)
    for build in specs.builds:
        image_name_no_tag = construct_full_image_name(build.name, appname)
        image_tag = build.tag if build.tag else release.tag
        dockerfile = build.dockerfile
        if dockerfile is None:
            dockerfile = os.path.join(repo_dir, "Dockerfile")
        full_image_name = "{}:{}".format(image_name_no_tag, image_tag)

        # use docker to build image
        try:
            build_args_dict = {
                "path": repo_dir,
                "dockerfile": dockerfile,
                "tag": full_image_name,
            }
            if build.target:
                build_args_dict['target'] = build.target
            if build.args:
                build_args_dict['buildargs'] = build.args

            for line in client.build(**build_args_dict):
                output_dict = json.loads(line.decode('utf8'))
                if 'stream' in output_dict:
                    yield make_msg("Building", raw_data=output_dict, msg=output_dict['stream'].rstrip("\n"))
        except docker.errors.APIError as e:
            raise BuildError(make_msg("Building", success=False, error="Building error: {}".format(str(e))))
        try:
            for line in client.push(full_image_name, stream=True):
                output_dict = json.loads(line.decode('utf8'))
                msg = "{}:{}\n".format(output_dict.get('id'), output_dict.get('status'))
                yield make_msg("Pushing", raw_data=output_dict, msg=msg.rstrip("\n"))
        except docker.errors.APIError as e:
            raise BuildError(make_msg("Pushing", success=False, error="pushing error: {}".format(str(e))))
        logger.debug("=========", full_image_name)
    yield make_msg("Finished", msg="build app {}'s release {} successfully".format(appname, git_tag))

