# -*- coding: utf-8 -*-
import redis_lock
from celery import current_app

from console.config import TASK_PUBSUB_CHANNEL
from console.ext import rds, db
from console.libs.utils import logger, save_job_log, BuildError
from console.libs.utils import build_image as _build_image
from console.libs.k8s import kube_api, ApiException
from console.libs import sse
from console.models import Job, Release


@current_app.task(bind=True)
def build_image(self, appname, git_tag):
    release = Release.get_by_app_and_tag(appname, git_tag)
    try:
        for msg in _build_image(appname, release):
            self.stream_output(msg)
    except BuildError as e:
        self.stream_ouput(e.data)


def _handle_job_pod_event(job, obj):
    jobname = job.name
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
        save_pod_log.delay(jobname, pod_name, job.version)


@current_app.task(bind=True)
def watch_app_pods(self):
    lock_name = "__celery_lck_kae_app_pods_watcher"

    lock = redis_lock.Lock(rds, lock_name)
    if not lock.acquire(blocking=False):
        return "Already running"
    else:
        lock.release()

    last_seen_version = None
    with redis_lock.Lock(rds, lock_name, expire=30, auto_renewal=True):
        label_selector = "kae-type in (app, job)"
        while True:
            try:
                if last_seen_version is not None:
                    watcher = kube_api.watch_pods(label_selector=label_selector, resource_version=last_seen_version)
                else:
                    watcher = kube_api.watch_pods(label_selector=label_selector)

                for event in watcher:
                    obj = event['object']
                    labels = obj.metadata.labels or {}
                    last_seen_version = obj.metadata.resource_version

                    if 'kae-app-name' in labels:
                        appname = labels['kae-app-name']
                        channel = "kae-app-{}-pods-watcher".format(appname)
                        # print(channel)
                        type = 'pod'
                        data = {
                            'object': event['raw_object'],
                            'action': event['type'],
                        }
                        sse.publish(data, type=type, channel=channel)
                    elif 'kae-job-name' in labels:
                        if event['type'] == 'DELETED':
                            continue
                        jobname = labels['kae-job-name']
                        try:
                            job = Job.get_by_name(jobname)
                        except:
                            logger.exception("failed to get job {}".format(jobname))
                            continue
                        finally:
                            # stupid workaround for issue: Can't reconnect until invalid transaction is rolled back
                            # this issue cause by mysql, mysql will close idle connection, and if you didn't close or rollback the session
                            # then when sqlalchemy try to reconnect, it will get this problem
                            db.session.close()

                        if not job:
                            continue
                        _handle_job_pod_event(job, event['raw_object'])
            except Exception as e:
                # logger.error("---------watch error ------------------")
                logger.exception("watch pods workers error")
                logger.error("watch pods error {}".format(str(e)))


@current_app.task
def update_job_status():
    pass


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


@current_app.task
def check_app_pods_watcher():
    lock_name = "__celery_lck_kae_app_pods_watcher"

    lock = redis_lock.Lock(rds, lock_name)
    if lock.acquire(blocking=False):
        lock.release()
        watch_app_pods.delay()


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
