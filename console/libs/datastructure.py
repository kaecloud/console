# -*- coding: utf-8 -*-

from boltons.iterutils import remap
from datetime import datetime
from werkzeug.routing import BaseConverter, ValidationError


class DateConverter(BaseConverter):
    """Extracts a ISO8601 date from the path and validates it."""

    regex = r'\d{4}-\d{2}-\d{2}'

    def to_python(self, value):
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            raise ValidationError()

    def to_url(self, value):
        return value.strftime('%Y-%m-%d')


def purge_none_val_from_dict(dic):
    '''
    >>> purge_none_val_from_dict({'foo': None})
    {}
    '''
    return remap(dic, visit=lambda path, key, val: val is not None)


