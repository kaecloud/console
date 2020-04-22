# -*- coding: utf-8 -*-
from celery import current_app
from celery.exceptions import SoftTimeLimitExceeded

from console.config import TASK_PUBSUB_CHANNEL, APP_BUILD_TIMEOUT
from console.ext import rds, db
from console.libs.utils import logger, save_job_log, BuildError, build_image_helper, make_errmsg
from console.libs.k8s import KubeApi, ApiException
from console.models import Release


@current_app.task(bind=True, soft_time_limit=APP_BUILD_TIMEOUT)
def build_image(self, appname, git_tag):
    release = Release.get_by_app_and_tag(appname, git_tag)
    try:
        for msg in build_image_helper(appname, release):
            self.stream_output(msg)
    except BuildError as e:
        self.stream_output(e.data)
    except SoftTimeLimitExceeded:
        logger.warn("build timeout.")
        self.stream_output(make_errmsg('build timeout, please test in local environment and contact administrator'))


def celery_task_stream_response(celery_task_ids, timeout=0, exit_when_timeout=True):
    if isinstance(celery_task_ids, str):
        celery_task_ids = celery_task_ids,

    task_progress_channels = [TASK_PUBSUB_CHANNEL.format(task_id=id_) for id_ in celery_task_ids]
    pubsub = rds.pubsub()
    pubsub.subscribe(task_progress_channels)
    try:
        while pubsub.subscribed:
            resp = pubsub.get_message(timeout=timeout)
            if resp is None:
                if exit_when_timeout:
                    logger.warn("pubsub timeout {}".format(celery_task_ids))
                    return None
                continue
            raw_content = resp['data']
            # omit the initial message where item['data'] is 1L
            if not isinstance(raw_content, (bytes, str)):
                continue
            content = raw_content
            if isinstance(content, bytes):
                content = content.decode('utf-8')
            logger.debug('Got pubsub message: %s', content)
            # task will publish TASK_PUBSUB_EOF at success or failure
            if content.startswith('CELERY_TASK_DONE'):
                finished_task_id = content[content.find(':') + 1:]
                finished_task_channel = TASK_PUBSUB_CHANNEL.format(task_id=finished_task_id)
                logger.debug('Task %s finished, break celery_task_stream_response', finished_task_id)
                pubsub.unsubscribe(finished_task_channel)
            else:
                yield content
    finally:
        logger.debug("celery stream response exit ************")
        pubsub.unsubscribe()
        pubsub.close()
