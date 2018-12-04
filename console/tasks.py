# -*- coding: utf-8 -*-
from celery import current_app
from celery.exceptions import SoftTimeLimitExceeded

from console.config import TASK_PUBSUB_CHANNEL, APP_BUILD_TIMEOUT
from console.ext import rds, db
from console.libs.utils import logger, save_job_log, BuildError, build_image_helper, make_errmsg
from console.libs.k8s import kube_api, ApiException
from console.models import Release, Job


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


@current_app.task
def handle_job_pod_event(jobname, obj):
    job = Job.get_by_name(jobname)
    if not job:
        return
    state = {}
    job_update = {}
    status = (None, None)
    if 'containerStatuses' in obj['status']:
        state = obj['status']['containerStatuses'][0]['state']
        for k, v in state.items():
            status = (k, v.get('reason', None))
            status_str = '{}: {}'.format(*status)
            continue
        job_update['state'] = state
    elif obj['status']['phase'] == 'Pending':
        job.update_status("Pending")
        return
    else:
        status_str = '{}: {}'.format(obj['status']['phase'], obj['status']['reason'])

    pod_name = obj['metadata']['name']

    # # update Running Node & used GPU
    # if status[0] == 'terminated':
    #     node_used_gpus[obj['spec']['nodeName']].pop(pod_name, None)
    # elif status[0] == 'waiting':  # waiting doesn't use GPU
    #     pass
    # elif obj['spec'].get('nodeName', None):
    #     job_update['runningNode'] = obj['spec']['nodeName']
    #     node_used_gpus[obj['spec']['nodeName']][pod_name] = int(job_exist['gpuNum'])

    # # Job is being terminated should not affect job status
    # if labels.get('ktqueue-terminating', None) == 'true':
    #     return

    # logging.info('Job {} enter state {}'.format(job_name, status_str))

    # update status
    if status == ('terminated', 'Completed'):
        job_update['status'] = 'Completed'
    elif status == ('running', None):
        job_update['status'] = 'Running'
    else:
        job_update['status'] = status_str

    job.update_status(job_update['status'])

    # When a job is successful finished, save log
    if status[0] == 'terminated':
        save_pod_log(jobname, pod_name, job.version)


@current_app.task
def save_pod_log(jobname, podname, version=0):
    try:
        resp = kube_api.get_pod_log(podname=podname)
    except ApiException as e:
        if e.status == 404:
            return
        else:
            raise e
    try:
        save_job_log(job_name=jobname, resp=resp, version=version)
    except:
        logger.exception("Error when get pod log")


def celery_task_stream_response(celery_task_ids, timeout=0, exit_when_timeout=True):
    if isinstance(celery_task_ids, str):
        celery_task_ids = celery_task_ids,

    task_progress_channels = [TASK_PUBSUB_CHANNEL.format(task_id=id_) for id_ in celery_task_ids]
    pubsub = rds.pubsub()
    pubsub.subscribe(task_progress_channels)
    try:
        while True:
            resp = pubsub.get_message(timeout=timeout)
            if resp is None:
                if exit_when_timeout:
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
        pubsub.unsubscribe()
        pubsub.close()
