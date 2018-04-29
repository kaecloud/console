# -*- coding: utf-8 -*-
import json
import logging
import requests
from etcd import EtcdException
from flask import session
from functools import wraps, partial

from console.config import NOTBOT_SENDMSG_URL, LOGGER_NAME, DEBUG
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


handle_etcd_exception = partial(handle_exception, (EtcdException, ValueError, KeyError))


def login_user(user):
    session['id'] = user.id
    session['name'] = user.name


def shorten_sentence(s, length=88):
    if len(s) > length:
        return s[:length]
    return s


def notbot_sendmsg(to, content, subject='console message'):
    if not all([to, content, NOTBOT_SENDMSG_URL]):
        return
    to = to.strip(';')
    if DEBUG:
        logger.debug('Sending notbot message to %s, content: %s', to, content)
        return
    content = '[console] {}'.format(content)
    try:
        res = requests.post(NOTBOT_SENDMSG_URL, {'to': to, 'content': content, subject: subject})
    except:
        logger.error('Send notbot msg failed, got code %s, response %s', res.status_code, res.rext)
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
