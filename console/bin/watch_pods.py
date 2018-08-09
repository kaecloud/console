# -*- coding: utf-8 -*-
import json
import argparse
import threading
from urllib3.exceptions import ProtocolError

# must import celery before import tasks
from console.app import celery
from console.libs.utils import logger
from console.libs.k8s import kube_api
from console.libs.utils import make_app_watcher_channel_name
from console.libs.jsonutils import VersatileEncoder
from console.tasks import handle_job_pod_event
from console.ext import rds


def spawn(target, *args, **kw):
    t = threading.Thread(target=target, name=target.__name__, args=args, kwargs=kw)
    t.daemon = True
    t.start()
    return t


class LongRunningWatcher(object):
    def __init__(self, sync=False):
        self.sync = sync
        self.thread_map = {}

    def start(self):
        for name in kube_api.cluster_names:
            logger.info("create watcher thread for cluster {}".format(name))
            self.thread_map[name] = spawn(self.watch_app_job_pods, name)

    def wait(self):
        while True:
            for name, t in self.thread_map.items():
                t.join(30)
                if not t.isAlive():
                    logger.info("cluster {}'s watcher thread crashed, restart it".format(name))
                    self.thread_map[name] = spawn(self.watch_app_job_pods, name)

    def watch_app_job_pods(self, cluster):
        last_seen_version = None
        label_selector = "kae-type in (app, job)"
        while True:
            try:
                if last_seen_version is not None:
                    watcher = kube_api.watch_pods(cluster_name=cluster, label_selector=label_selector, resource_version=last_seen_version)
                else:
                    watcher = kube_api.watch_pods(cluster_name=cluster, label_selector=label_selector)

                for event in watcher:
                    obj = event['object']
                    labels = obj.metadata.labels or {}
                    last_seen_version = obj.metadata.resource_version

                    if 'kae-app-name' in labels:
                        appname = labels['kae-app-name']
                        channel = make_app_watcher_channel_name(cluster, appname)
                        data = {
                            'object': obj.to_dict(),
                            'action': event['type'],
                        }
                        rds.publish(message=json.dumps(data, cls=VersatileEncoder), channel=channel)
                    elif 'kae-job-name' in labels:
                        if event['type'] == 'DELETED':
                            continue
                        jobname = labels['kae-job-name']
                        handle_job_pod_event.delay(jobname, event['raw_object'])
            except ProtocolError:
                logger.warn('skip this error... because kubernetes disconnect client after default 10m...')
            except Exception as e:
                # logger.error("---------watch error ------------------")
                logger.exception("watch pods workers error")
                # logger.error("watch pods error {}".format(str(e)))


def parse_args():
    parser = argparse.ArgumentParser(description='Watch pods')
    parser.add_argument('--sync', action='store_true')
    args = parser.parse_args()
    return args


if __name__ == '__main__':
    args = parse_args()
    wch = LongRunningWatcher(args.sync)
    wch.start()
    wch.wait()
