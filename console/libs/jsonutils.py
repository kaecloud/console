# -*- coding: utf-8 -*-

import json
from datetime import datetime
from decimal import Decimal
from flask import Response
from functools import wraps


class Jsonized:

    _raw = {}

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.__dict__ == other.__dict__
        return False

    def __str__(self):
        return str(self.__dict__)

    def to_dict(self):
        return self._raw


class VersatileEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, 'to_dict'):
            return obj.to_dict()
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%d %H:%M:%S')
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, bytes):
            return obj.decode('utf-8')
        return super(VersatileEncoder, self).default(obj)


def jsonize(f):
    @wraps(f)
    def _(*args, **kwargs):
        r = f(*args, **kwargs)
        data, code = r if isinstance(r, tuple) else (r, 200)
        try:
            return Response(json.dumps(data, cls=VersatileEncoder, ensure_ascii=False), status=code, mimetype='application/json')
        except TypeError:
            # data could be flask.Response objects, e.g. redirect responses
            return data
    return _
