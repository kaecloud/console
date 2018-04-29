# -*- coding: utf-8 -*-

import json

from git import Repo
from celery import current_app
from humanfriendly import parse_timespan
import delegator

from console.config import TASK_PUBSUB_CHANNEL
from console.ext import rds
from console.libs.exceptions import ActionError
from console.libs.utils import notbot_sendmsg, logger
from console.models import Release


@current_app.task(bind=True)
def build_image(self, appname, sha, spec):
    release = Release.get_by_app_and_sha(appname, sha)
    specs = release.specs
    if release.raw:
        release.update_image(specs.base)
        return

    # use kaniko to build image
    d = delegator.run("executor --dockerfile={} --context={} --destination={}".format(dockerfile, context, image), block=False)
    while True:
        m = d.std_out.readline()
        if m == '' or d.is_alive() is False:
            break
        self.stream_output(m)
    if d.return_code:
        self.stream_output(d.err())

    image_tag = m.progress
    release.update_image(image_tag)
    return image_tag


def celery_task_stream_response(celery_task_ids):
    if isinstance(celery_task_ids, str):
        celery_task_ids = celery_task_ids,

    task_progress_channels = [TASK_PUBSUB_CHANNEL.format(task_id=id_) for id_ in celery_task_ids]
    pubsub = rds.pubsub()
    pubsub.subscribe(task_progress_channels)
    for item in pubsub.listen():
        # each content is a single JSON encoded grpc message
        raw_content = item['data']
        # omit the initial message where item['data'] is 1L
        if not isinstance(raw_content, (bytes, str)):
            continue
        content = raw_content.decode('utf-8')
        logger.debug('Got pubsub message: %s', content)
        # task will publish TASK_PUBSUB_EOF at success or failure
        if content.startswith('CELERY_TASK_DONE'):
            finished_task_id = content[content.find(':') + 1:]
            finished_task_channel = TASK_PUBSUB_CHANNEL.format(task_id=finished_task_id)
            logger.debug('Task %s finished, break celery_task_stream_response', finished_task_id)
            pubsub.unsubscribe(finished_task_channel)
        else:
            yield content


