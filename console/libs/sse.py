# coding=utf-8
from __future__ import unicode_literals

from collections import OrderedDict
from flask import json
import six

from console.ext import rds


@six.python_2_unicode_compatible
class Message(object):
    """
    Data that is published as a server-sent event.
    """
    def __init__(self, data, type=None, id=None, retry=None):
        """
        Create a server-sent event.
        :param data: The event data. If it is not a string, it will be
            serialized to JSON using the Flask application's
            :class:`~flask.json.JSONEncoder`.
        :param type: An optional event type.
        :param id: An optional event ID.
        :param retry: An optional integer, to specify the reconnect time for
            disconnected clients of this stream.
        """
        self.data = data
        self.type = type
        self.id = id
        self.retry = retry

    def to_dict(self):
        """
        Serialize this object to a minimal dictionary, for storing in Redis.
        """
        # data is required, all others are optional
        d = {"data": self.data}
        if self.type:
            d["type"] = self.type
        if self.id:
            d["id"] = self.id
        if self.retry:
            d["retry"] = self.retry
        return d

    def __str__(self):
        """
        Serialize this object to a string, according to the `server-sent events
        specification <https://www.w3.org/TR/eventsource/>`_.
        """
        if isinstance(self.data, six.string_types):
            data = self.data
        else:
            data = json.dumps(self.data)
        lines = ["data:{value}".format(value=line) for line in data.splitlines()]
        if self.type:
            lines.insert(0, "event:{value}".format(value=self.type))
        if self.id:
            lines.append("id:{value}".format(value=self.id))
        if self.retry:
            lines.append("retry:{value}".format(value=self.retry))
        return "\n".join(lines) + "\n\n"

    def __repr__(self):
        kwargs = OrderedDict()
        if self.type:
            kwargs["type"] = self.type
        if self.id:
            kwargs["id"] = self.id
        if self.retry:
            kwargs["retry"] = self.retry
        kwargs_repr = "".join(
            ", {key}={value!r}".format(key=key, value=value)
            for key, value in kwargs.items()
        )
        return "{classname}({data!r}{kwargs})".format(
            classname=self.__class__.__name__,
            data=self.data,
            kwargs=kwargs_repr,
        )

    def __eq__(self, other):
        return (
            isinstance(other, self.__class__) and
            self.data == other.data and
            self.type == other.type and
            self.id == other.id and
            self.retry == other.retry
        )


def publish(data, type=None, id=None, retry=None, channel='sse'):
    """
    Publish data as a server-sent event.
    :param data: The event data. If it is not a string, it will be
        serialized to JSON using the Flask application's
        :class:`~flask.json.JSONEncoder`.
    :param type: An optional event type.
    :param id: An optional event ID.
    :param retry: An optional integer, to specify the reconnect time for
        disconnected clients of this stream.
    :param channel: If you want to direct different events to different
        clients, you may specify a channel for this event to go to.
        Only clients listening to the same channel will receive this event.
        Defaults to "sse".
    """
    message = Message(data, type=type, id=id, retry=retry)
    msg_json = json.dumps(message.to_dict())
    return rds.publish(channel=channel, message=msg_json)


def messages(channel='sse'):
    """
    A generator of :class:`~flask_sse.Message` objects from the given channel.
    """
    pubsub = rds.pubsub()
    pubsub.subscribe(channel)
    for pubsub_message in pubsub.listen():
        if pubsub_message['type'] == 'message':
            msg_dict = json.loads(pubsub_message['data'])
            yield Message(**msg_dict)


def make_msg(msg, type=None):
    if isinstance(msg, str):
        msg = {'data': msg}
    return str(Message(data=msg, type=type))


def make_errmsg(msg):
    return str(Message(data={'error': msg}, type='close'))


def make_close_msg(msg):
    return make_msg(msg, type='close')
