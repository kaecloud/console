# -*- coding: utf-8 -*-
import time
import json
import string
import random
import logging
import requests
from flask import session
from functools import wraps

from console.config import BOT_WEBHOOK_URL, LOGGER_NAME, DEBUG, DEFAULT_REGISTRY
from console.libs.jsonutils import VersatileEncoder


logger = logging.getLogger(LOGGER_NAME)


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


def bearychat_sendmsg(to, content):
    if not all([to, content, BOT_WEBHOOK_URL]):
        return
    to = to.strip(';')
    if DEBUG:
        logger.debug('Sending notbot message to %s, content: %s', to, content)
        return
    content = '[console] {}'.format(content)
    data = {
        "text": content,
        "channel": to,
    }
    try:
        res = requests.post(BOT_WEBHOOK_URL, data, headers={'Content-Type': 'application/json'})
    except:
        logger.exception('Send bearychat msg failed')
        return
    return res


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


def id_generator(size=6, chars=string.ascii_uppercase + string.digits):
    return ''.join(random.choice(chars) for _ in range(size))


def generate_unique_dirname(prefix=None):
    time_str = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
    if prefix is None:
        name = "{}_{}".format(time_str, id_generator(8))
    else:
        name = "{}_{}_{}".format(prefix, time_str, id_generator(8))
    return name


def parse_image_name(image_name):
    parts = image_name.split('/', 1)
    if '.' in parts[0]:
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