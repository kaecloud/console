import os
import time
import yaml
import logging
import json
import base64
import copy
from addict import Dict
from kubernetes import client, config, watch
from kubernetes.watch.watch import iter_resp_lines
from kubernetes.stream import stream

from kubernetes.client.rest import ApiException

from console.config import (
    HOST_VOLUMES_DIR, POD_LOG_DIR, CLUSTER_BASE_DOMAIN_MAP,
    REGISTRY_AUTHS, DFS_VOLUME, DFS_MOUNT_DIR, JOBS_ROOT_DIR, JOBS_OUPUT_ROOT_DIR,
    INGRESS_ANNOTATIONS_PREFIX,
)

from .utils import parse_image_name, id_generator, make_canary_appname, search_tls_secret


class KubeError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg


class KubernetesApi(object):
    _INSTANCE = None
    ALL_CLUSTER = "__all_cluster__"
    DEFAULT_CLUSTER = "__default_cluster__"

    def __init__(self):
        self.cluster_map = {}
        self.default_cluster_name = None
        self._load_multiple_clients()

    @classmethod
    def instance(cls):
        if cls._INSTANCE is None:
            cls._INSTANCE = cls()
        return cls._INSTANCE

    @property
    def cluster_names(self):
        return list(self.cluster_map.keys())

    def cluster_exist(self, cluster_name):
        return cluster_name in self.cluster_map

    def _load_multiple_clients(self):
        if os.path.exists(os.path.expanduser("~/.kube/config")):
            contexts, active_context = config.list_kube_config_contexts()
            if not contexts:
                raise Exception("no context in kubeconfig")
            self.default_cluster_name = active_context['name']

            contexts = [context['name'] for context in contexts]
            for ctx in contexts:
                core_v1api = client.CoreV1Api(
                    api_client=config.new_client_from_config(context=ctx))
                extensions_api = client.ExtensionsV1beta1Api(
                    api_client=config.new_client_from_config(context=ctx))
                batch_api = client.BatchV1Api(
                    api_client=config.new_client_from_config(context=ctx))
                self.cluster_map[ctx] = ClientApiBundle(ctx, core_v1api, extensions_api, batch_api)
        else:
            config.load_incluster_config()
            core_v1api = client.CoreV1Api()
            extensions_api = client.ExtensionsV1beta1Api()
            batch_api = client.BatchV1Api()
            self.cluster_map['incluster'] = ClientApiBundle('incluster', core_v1api, extensions_api, batch_api)
            self.default_cluster_name = "incluster"

    def __getattr__(self, item):
        def wrapper(*args, **kwargs):
            cluster_name = kwargs.pop('cluster_name', self.default_cluster_name)
            if cluster_name == self.ALL_CLUSTER:
                results = {}
                for name, cluster in self.cluster_map.items():
                    func = getattr(cluster, item)
                    results[name] = func(*args, **kwargs)
                return results

            if cluster_name == self.DEFAULT_CLUSTER:
                cluster_name = self.default_cluster_name
            cluster = self.cluster_map.get(cluster_name, None)
            if cluster is None:
                raise Exception("cluster {} is not available".format(cluster_name))
            func = getattr(cluster, item)

            # deploy_app needs cluster argument to create ingress dict
            if item == "deploy_app":
                kwargs['cluster'] = cluster_name
            return func(*args, **kwargs)
        return wrapper


class ClientApiBundle(object):
    def __init__(self, name, core_v1api, extensions_api, batch_api):
        self.name = name
        self.core_v1api = core_v1api
        self.extensions_api = extensions_api
        self.batch_api = batch_api

    def create_job(self, spec, namespace='default'):
        body = self._create_job_dict(spec)
        self.batch_api.create_namespaced_job(body=body, namespace=namespace)

    def delete_job(self, jobname, namespace='default', ignore_404=False):
        try:
            self.batch_api.delete_namespaced_job(
                name=jobname, namespace=namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground",
                                            grace_period_seconds=5))
        except ApiException as e:
            if not (e.status == 404 and ignore_404 is True):
                raise e

    def get_job(self, jobname, namespace='default'):
        return self.batch_api.read_namespaced_job(jobname, namespace=namespace)

    def get_pods(self, label_selector, namespace='default'):
        return self.core_v1api.list_namespaced_pod(namespace=namespace, label_selector=label_selector)

    def get_pod_log(self, podname, namespace='default', **kwargs):
        kwargs.pop('follow', False)
        return self.core_v1api.read_namespaced_pod_log(name=podname, namespace=namespace, **kwargs)

    def follow_pod_log(self, podname, namespace='default', **kwargs):
        kwargs['_preload_content'] = False
        kwargs['follow'] = True
        resp = self.core_v1api.read_namespaced_pod_log(name=podname, namespace=namespace, **kwargs)
        for line in iter_resp_lines(resp):
            yield line

    def get_job_pods(self, jobname, namespace='default'):
        label_selector = "kae-job-name={}".format(jobname)
        return self.core_v1api.list_namespaced_pod(namespace=namespace, label_selector=label_selector)

    def get_app_pods(self, name, namespace="default"):
        label_selector = "kae-app-name={}".format(name)
        return self.core_v1api.list_namespaced_pod(namespace=namespace, label_selector=label_selector)

    def watch_pods(self, label_selector=None, **kwargs):
        if label_selector is None:
            label_selector = "kae=true"
        w = watch.Watch()
        return w.stream(self.core_v1api.list_pod_for_all_namespaces, label_selector=label_selector, **kwargs)

    def exec_shell(self, podname, namespace='default', container=None):
        exec_command = ['/bin/sh']
        kwargs = {
            "command": exec_command,
            "stderr": True,
            "stdin": True,
            "stdout": True,
            "tty": True,
            "_preload_content": False,
        }
        if container:
            kwargs['container'] = container
        resp = stream(self.core_v1api.connect_get_namespaced_pod_exec, podname, namespace, **kwargs)
        return resp

    def create_or_update_config_map(self, appname, cm_data, replace=True, namespace="default"):
        """
        create or update configmap for specfied app
        :param appname:
        :param cm_data: configmap data dict
        :param replace: if it set to True, the existing configmap data is replaced by cm_data, otherwise cm_data is merged to the exising data
        :param namespace:
        :return:
        """
        try:
            cmap = self.core_v1api.read_namespaced_config_map(name=appname, namespace=namespace)
            if replace:
                cmap.data = cm_data
            else:
                cmap.data.update(cm_data)
            self.core_v1api.replace_namespaced_config_map(name=appname, namespace=namespace, body=cmap)
        except ApiException as e:
            if e.status == 404:
                cmap = client.V1ConfigMap()
                cmap.metadata = client.V1ObjectMeta(name=appname)
                cmap.data = cm_data
                self.core_v1api.create_namespaced_config_map(namespace=namespace, body=cmap)
            else:
                raise e

    def get_config_map(self, appname, namespace="default"):
        result = self.core_v1api.read_namespaced_config_map(name=appname, namespace=namespace)
        return result.data

    def delete_config_map(self, appname, namespace="default"):
        self.core_v1api.delete_namespaced_config_map(name=appname, namespace=namespace, body=client.V1DeleteOptions())

    def create_or_update_secret(self, appname, secrets, replace=True, namespace="default"):
        """
        create or update secret for specified app
        :param appname:
        :param secrets: new secret data dict
        :param replace: replace the existing secret data or just merge new data to the existing secret data
        :param namespace:
        :return:
        """
        base64_secrets = {}
        for k, v in secrets.items():
            b = v
            if isinstance(b, str):
                b = v.encode("utf8")
            if not isinstance(b, bytes):
                raise ValueError("secret value should be string or dict")
            base64_secrets[k] = base64.b64encode(b).decode('utf8')
        try:
            sec = self.core_v1api.read_namespaced_secret(name=appname, namespace=namespace)
            if replace:
                sec.data = base64_secrets
            else:
                sec.data.update(base64_secrets)
            self.core_v1api.replace_namespaced_secret(name=appname, namespace=namespace, body=sec)
        except ApiException as e:
            if e.status == 404:
                sec = client.V1Secret()
                sec.metadata = client.V1ObjectMeta(name=appname)
                sec.type = "Opaque"
                sec.data = base64_secrets
                self.core_v1api.create_namespaced_secret(namespace=namespace, body=sec)
            else:
                raise e

    def get_secret(self, appname, namespace="default"):
        result = self.core_v1api.read_namespaced_secret(name=appname, namespace=namespace)
        secrets = {}
        if not result.data:
            return secrets

        for k, base64_v in result.data.items():
            v = base64.b64decode(base64_v).decode('utf8')
            secrets[k] = v

        return secrets

    def delete_secret(self, appname, namespace="default"):
        self.core_v1api.delete_namespaced_secret(name=appname, namespace=namespace, body=client.V1DeleteOptions())

    def apply(self, d, namespace="default"):
        """
        create or update deplopyment, service, ingress
        :param d:
        :param namespace:
        :return:
        """
        kind = d["kind"]
        name = d["metadata"]["name"]
        if kind == "Deployment":
            try:
                self.extensions_api.replace_namespaced_deployment(name=name, body=d, namespace=namespace)
            except ApiException as e:
                if e.status == 404:
                    self.extensions_api.create_namespaced_deployment(body=d, namespace=namespace)
                else:
                    raise e
        elif kind == "Service":
            self.create_or_update_service(d, namespace)
        elif kind == "Ingress":
            try:
                self.extensions_api.replace_namespaced_ingress(name=name, body=d, namespace=namespace)
            except ApiException as e:
                if e.status == 404:
                    self.extensions_api.create_namespaced_ingress(body=d, namespace=namespace)
                else:
                    raise e

    def create_or_update_service(self, d, namespace="default"):
        name = d['metadata']['name']
        try:
            result = self.core_v1api.read_namespaced_service(name=name, namespace=namespace)
            d['metadata']['resourceVersion'] = result.metadata.resource_version
            d['spec']['clusterIP'] = result.spec.cluster_ip
            self.core_v1api.replace_namespaced_service(name=name, body=d, namespace=namespace)
        except ApiException as e:
            if e.status == 404:
                self.core_v1api.create_namespaced_service(body=d, namespace=namespace)
            else:
                raise e

    def renew_app(self, appname, namespace='default'):
        """
        force kubernetes to recreate the pods, it mainly used to make secrets and configmap effective.
        :param appname:
        :param namespace:
        :return:
        """
        deployment = self.extensions_api.read_namespaced_deployment(name=appname, namespace=namespace)

        if deployment.spec.template.metadata.annotations is None:
            deployment.spec.template.metadata.annotations = {}
        deployment.spec.template.metadata.annotations['renew_id'] = id_generator(10)
        self.extensions_api.replace_namespaced_deployment(name=appname, namespace=namespace, body=deployment)

    def deploy_app(self, spec, release_tag, cluster, namespace="default"):
        ing = None
        dp_annotations = {
            'release_tag': release_tag,
        }
        dp = self._create_deployment_dict(spec, annotations=dp_annotations)
        svc = self._create_service_dict(spec)

        if spec.type == "web":
            ing = self._create_ingress_dict(spec, cluster)
        self.apply(dp, namespace=namespace)
        self.apply(svc, namespace=namespace)
        if ing is not None:
            self.apply(ing, namespace=namespace)

    def deploy_app_canary(self, spec, release_tag, namespace="default"):
        """
        create Canary Deployment for specified app.
        """
        spec_copy = copy.deepcopy(spec)

        dp_annotations = {
            'spec': yaml.dump(spec_copy.to_dict()),
            'release_tag': release_tag,
        }
        dp_dict = self._create_deployment_dict(spec_copy, annotations=dp_annotations, canary=True)
        svc_dict = self._create_service_dict(spec_copy, canary=True)

        self.apply(dp_dict, namespace=namespace)
        self.apply(svc_dict, namespace=namespace)

    def set_abtesting_rules(self, appname, rules, namespace="default"):
        canary_appname = make_canary_appname(appname)
        annotations_key = "{}/abtesting".format(INGRESS_ANNOTATIONS_PREFIX)
        ing = self.extensions_api.read_namespaced_ingress(appname, namespace=namespace)
        data = {
            "backend": {
                "service": canary_appname,
                # for web app, the service port is 80
                "port": 80,
            },
            "rules": rules,
        }
        annotations = ing.metadata.annotations if ing.metadata.annotations else {}
        annotations[annotations_key] = json.dumps(data)
        ing.metadata.annotations = annotations
        self.extensions_api.replace_namespaced_ingress(name=appname, body=ing, namespace=namespace)

    def get_abtesting_rules(self, appname, namespace="default"):
        annotations_key = "{}/abtesting".format(INGRESS_ANNOTATIONS_PREFIX)
        ing = self.extensions_api.read_namespaced_ingress(appname, namespace=namespace)
        annotations = ing.metadata.annotations if ing.metadata.annotations else {}
        full_rules_str = annotations.get(annotations_key, None)
        if full_rules_str is None:
            return None
        full_rules = json.loads(full_rules_str)
        return full_rules.get("rules", None)

    def undeploy_app_canary(self, appname, namespace="default", ignore_404=False):
        canary_appname = make_canary_appname(appname)
        annotations_key = "{}/abtesting".format(INGRESS_ANNOTATIONS_PREFIX)
        # remove abtesting rules
        try:
            ing = self.extensions_api.read_namespaced_ingress(appname, namespace=namespace)

            annotations = ing.metadata.annotations if ing.metadata.annotations else {}
            if annotations_key in annotations:
                ing.metadata.annotations.pop(annotations_key)
                self.extensions_api.replace_namespaced_ingress(name=appname, body=ing, namespace=namespace)
                # the nginx-ingress needs about 1 seconds to detect the change of the ingress
                time.sleep(1)
        except ApiException as e:
            if not (e.status == 404 and ignore_404 is True):
                raise e
        # remove sevice
        try:
            self.core_v1api.delete_namespaced_service(
                name=canary_appname, namespace=namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground",
                                            grace_period_seconds=5))
        except ApiException as e:
            if not (e.status == 404 and ignore_404 is True):
                raise e
        # remove deployment
        try:
            self.extensions_api.delete_namespaced_deployment(
                name=canary_appname, namespace=namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground",
                                            grace_period_seconds=5))
        except ApiException as e:
            if not (e.status == 404 and ignore_404 is True):
                raise e

    def update_app(self, appname, spec, release_tag, namespace='default', version=None, renew_id=None):
        dp_annotations = {
            'release_tag': release_tag,
        }
        d = self._create_deployment_dict(spec, version=version, renew_id=renew_id, annotations=dp_annotations)
        self.extensions_api.replace_namespaced_deployment(name=appname, namespace=namespace, body=d)

    def rollback_app(self, appname, revision=0, namespace="default"):
        rollback_to = client.ExtensionsV1beta1RollbackConfig()
        rollback_to.revision = revision

        # name and rollback_to arguments can't be None
        rollback = client.ExtensionsV1beta1DeploymentRollback(
            name=appname,
            rollback_to=rollback_to,
            api_version="extensions/v1beta1",
            kind="DeploymentRollback",
        )
        self.extensions_api.create_namespaced_deployment_rollback(name=appname, namespace=namespace, body=rollback)

    def undeploy_app(self, appname, apptype, namespace='default', ignore_404=False):
        self.undeploy_app_canary(appname, namespace=namespace, ignore_404=True)

        # delete resource in the following order: ingress, service, deployment, secret, configmap
        if apptype == "web":
            try:
                self.extensions_api.delete_namespaced_ingress(
                    name=appname, namespace=namespace,
                    body=client.V1DeleteOptions(propagation_policy="Foreground",
                                                grace_period_seconds=5))
            except ApiException as e:
                if not (e.status == 404 and ignore_404 is True):
                    raise e

        if apptype in ("worker", "web"):
            try:
                self.core_v1api.delete_namespaced_service(
                    name=appname, namespace=namespace,
                    body=client.V1DeleteOptions(propagation_policy="Foreground",
                                                grace_period_seconds=5))
            except ApiException as e:
                if not (e.status == 404 and ignore_404 is True):
                    raise e
        try:
            self.extensions_api.delete_namespaced_deployment(
                name=appname, namespace=namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground",
                                            grace_period_seconds=5))
        except ApiException as e:
            if not (e.status == 404 and ignore_404 is True):
                raise e

        try:
            self.delete_secret(appname, namespace)
        except ApiException as e:
            if e.status != 404:
                raise e
        try:
            self.delete_config_map(appname, namespace)
        except ApiException as e:
            if e.status != 404:
                raise e

    def get_deployment(self, name, namespace='default', ignore_404=False):
        """
        get kubernetes deployment object
        :param name:
        :param namespace:
        :return:
        """
        try:
            return self.extensions_api.read_namespaced_deployment(name=name, namespace=namespace)
        except ApiException as e:
            if e.status == 404 and ignore_404 is True:
                return None
            else:
                raise e

    @classmethod
    def _construct_pod_spec(cls, name, volumes_root, container_spec_list,
                            restartPolicy='Always', initial_env=None,
                            initial_vol_mounts=None, default_work_dir=None, secret_name=None):
        has_configmap = False
        if secret_name is None:
            secret_name = name

        pod_spec = Dict({
            'volumes': [],
        })
        copy_list = [
            'name', 'image', 'imagePullPolicy', 'args', 'command', 'tty',
            'workingDir', 'livenessProbe', 'readinessProbe', 'ports',
        ]
        containers = []
        images = []
        for container_spec in container_spec_list:
            c = Dict()
            for attr in copy_list:
                if container_spec[attr]:
                    c[attr] = copy.deepcopy(container_spec[attr])
                else:
                    if attr == 'workingDir' and default_work_dir is not None:
                        c['workingDir'] = default_work_dir

            containers.append(c)
            images.append(container_spec.image)
        # update imagePullSecrets
        imagePullSecrets = []
        for image in images:
            registry, img_name = parse_image_name(image)
            registry = registry if registry else "https://index.docker.io/v1/"
            if registry in REGISTRY_AUTHS:
                imagePullSecrets.append({'name': REGISTRY_AUTHS[registry]})
        if imagePullSecrets:
            pod_spec.imagePullSecrets = imagePullSecrets
        # construct kubernetes container specs
        for c, container_spec in zip(containers, container_spec_list):
            envs = copy.deepcopy(initial_env) if initial_env else []
            if 'env' in container_spec:
                for line in container_spec['env']:
                    k, v = line.split('=')
                    envs.append({"name": k, "value": v})
            if envs:
                c.env = envs

            # create resource requests and limits(mainly for cpu and memory)
            reqs = {}
            limits = {}
            if container_spec.cpu:
                if container_spec.cpu.request:
                    reqs['cpu'] = container_spec.cpu.request
                if container_spec.cpu.limit:
                    limits['cpu'] = container_spec.cpu.limit
            if container_spec.memory:
                if container_spec.memory.request:
                    reqs['memory'] = container_spec.memory.request
                if container_spec.memory.limit:
                    limits['memory'] = container_spec.memory.limit
            if container_spec.gpu:
                limits['nvidia.com/gpu'] = container_spec.gpu
            c.resources = {}
            if reqs:
                c.resources['requests'] = reqs
            if limits:
                c.resources['limits'] = limits

            # mount log dir
            if initial_vol_mounts is None:
                initial_vol_mounts = []

            c.volumeMounts = copy.deepcopy(initial_vol_mounts) + container_spec['volumeMounts']

            if len(container_spec.configs) > 0:
                has_configmap = True
                for cfg in container_spec.configs:
                    container_abs_path = os.path.join(cfg.dir, cfg.filename)
                    volume_mount = {
                        "name": "configmap-volume",
                        "mountPath": container_abs_path,
                        "subPath": cfg.key,
                        "readOnly": True,
                    }
                    c.volumeMounts.append(volume_mount)

            if container_spec.secrets:
                for envname, key in zip(container_spec.secrets.envNameList, container_spec.secrets.keyList):
                    secret_ref = {
                        "name": envname,
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": secret_name,
                                "key": key,
                            }
                        }
                    }
                    if 'env' not in c:
                        c.env = []
                    c.env.append(secret_ref)

        if has_configmap:
            cfg_vol = {
                "name": "configmap-volume",
                "configMap": {
                    "name": secret_name,
                }
            }
            pod_spec.volumes.append(cfg_vol)

        pod_spec.containers = containers
        pod_spec.restartPolicy = restartPolicy
        return pod_spec

    @classmethod
    def _create_deployment_dict(cls, spec, version=None, renew_id=None, annotations=None, canary=False):
        # secret_name is the secret name and configmap name for this app
        secret_name = spec.appname
        if canary:
            appname = make_canary_appname(spec['appname'])
        else:
            appname = spec.appname
        svc = spec.service
        app_dir = os.path.join(HOST_VOLUMES_DIR, appname)
        host_kae_log_dir = os.path.join(app_dir, POD_LOG_DIR[1:])

        if annotations is None:
            annotations = {}

        obj = Dict({
            'apiVersion': 'extensions/v1beta1',
            'kind': 'Deployment',
            'metadata': {
                'name': appname,
                'labels': {
                    'kae': 'true',
                    'kae-type': 'app',
                    'kae-app-name': appname,
                },
                'annotations': annotations
            },
            'spec': {
                'replicas': svc.replicas,
                'selector': {
                    'matchLabels': {
                        'kae-app-name': appname,
                    }
                },
                'template': {
                    'metadata': {
                        'labels': {
                            'kae': 'true',
                            'kae-type': 'app',
                            'kae-app-name': appname,
                        },
                        'annotations': {
                        }
                    },
                    # 'spec': {},
                }
            }
        })

        if version is not None:
            obj.metadata.resourceVersion = str(version)
        if renew_id is not None:
            obj.spec.template.metadata.annotations['renew_id'] = renew_id
        if 'minReadySeconds' in svc.minReadySeconds:
            obj.spec.minReadySeconds = svc.minReadySeconds
        if 'progressDeadlineSeconds' in svc.progressDeadlineSeconds:
            obj.spec.progressDeadlineSeconds = svc.progressDeadlineSeconds
        if 'strategy' in svc:
            obj.spec.strategy = copy.deepcopy(svc.strategy)
        if 'labels' in svc:
            for line in svc.labels:
                k, v = line.split('=')
                obj.metadata.labels[k] = v

        log_mount = {
            "name": "kae-log-volumes",
            "mountPath": POD_LOG_DIR,
        }
        pod_spec = cls._construct_pod_spec(appname, app_dir, svc.containers, initial_vol_mounts=[log_mount], secret_name=secret_name)
        pod_spec.volumes.append(
            {
                "name": "kae-log-volumes",
                "hostPath": {
                    "path": host_kae_log_dir,
                    "type": "DirectoryOrCreate",
                }
            }
        )
        pod_spec.volumes = pod_spec.volumes + svc['volumes']

        obj.spec.template.spec = pod_spec
        return obj

    @classmethod
    def _create_service_dict(cls, spec, canary=False):
        if canary:
            appname = make_canary_appname(spec['appname'])
        else:
            appname = spec.appname

        svc = spec.service
        obj = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {
                'name': appname,
                'labels': {
                    'kae': 'true',
                    'kae-type': 'app',
                    'kae-app-name': appname,
                },
            },
            'spec': {
                'selector': {
                    'kae-app-name': appname,
                },
                "ports": svc.ports
            }
        }
        return obj

    @classmethod
    def _create_ingress_dict(cls, spec, cluster):
        appname = spec.appname
        svc = spec.service

        obj = Dict({
            'apiVersion': 'extensions/v1beta1',
            'kind': 'Ingress',
            'metadata': {
                'name': appname,
                'labels': {
                    'kae': 'true',
                    'kae-type': 'app',
                    'kae-app-name': appname,
                },
            },
            "spec": {
                "tls": [
                ],
                "rules": [

                ]
            }
        })

        # annotations
        ingress_annotations = svc.get("ingressAnnotations", None)
        if ingress_annotations:
            obj.metadata.annotations = copy.deepcopy(ingress_annotations)
        # https only
        https_only = svc.httpsOnly
        if https_only is False:
            annotations_key = "{}/ssl-redirect".format(INGRESS_ANNOTATIONS_PREFIX)
            obj.metadata.annotations[annotations_key] = "true" if https_only else "false"
        # parse mountpoints' host and path
        tls_list = []
        mp_cfg = {}
        for mp in svc.mountpoints:
            mp_cfg[mp.host] = mp.paths
            # if tlsSecret is specified, then use it, otherwise search from config
            tls_secret = mp.tlsSecret
            if not tls_secret:
                tls_secret = search_tls_secret(cluster, mp.host)
            if tls_secret:
                ingress_tls = {
                    "hosts": [
                        mp.host,
                    ],
                    "secretName": tls_secret,
                }
                tls_list.append(ingress_tls)

        cluster_domain = CLUSTER_BASE_DOMAIN_MAP.get(cluster, None)
        if cluster_domain is not None:
            default_domain = appname + '.' + cluster_domain
            if default_domain not in mp_cfg:
                mp_cfg[default_domain] = ['/']

                # setup tls
                tls_secret = search_tls_secret(cluster, default_domain)
                if tls_secret:
                    ingress_tls = {
                        "hosts": [
                            default_domain,
                        ],
                        "secretName": tls_secret,
                    }
                    tls_list.append(ingress_tls)

        # empty mp_cfg will cause an empty rules in ingress object
        if len(mp_cfg) == 0:
            raise KubeError("web app(cluster: {}) should at least have one host, add a host to mountpoints or check cluster's defaut host".format(cluster))

        for host, paths in mp_cfg.items():
            rule = {
                'host': host,
                'http': {
                    'paths': [
                    ]
                }
            }
            for path in paths:
                rule['http']['paths'].append({
                    'path': path,
                    'backend': {
                        'serviceName': appname,
                        'servicePort': 80
                    },

                })
            obj.spec.rules.append(rule)
        obj.spec.tls = tls_list
        return obj

    @classmethod
    def _create_job_dict(cls, spec):
        jobname = spec.jobname
        job_dir = os.path.join(JOBS_ROOT_DIR, jobname)
        if not os.path.exists(job_dir):
            os.makedirs(job_dir)
        output_dir = os.path.join(JOBS_OUPUT_ROOT_DIR, jobname)
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        work_dir = os.path.join(job_dir, 'code')

        initial_env = [
            {
                'name': 'JOB_NAME',
                'value': jobname
            },
            {
                'name': 'OUTPUT_DIR',
                'value': output_dir
            },
            {
                'name': 'WORK_DIR',
                'value': os.path.join(job_dir, 'code')
            },
            {
                'name': 'LC_ALL',
                'value': 'en_US.UTF-8'
            },
            {
                'name': 'LC_CTYPE',
                'value': 'en_US.UTF-8'
            },
        ]
        # when the .spec.template.spec.restartPolicy field is set to “OnFailure”, the back-off limit may be ineffective
        # see https://github.com/kubernetes/kubernetes/issues/54870
        restartPolicy = 'OnFailure' if spec.autoRestart else 'Never'

        pod_spec = cls._construct_pod_spec(jobname, job_dir, spec.containers, restartPolicy=restartPolicy,
                                           initial_env=initial_env, default_work_dir=work_dir)
        # add more config for job
        pod_spec.volumes.append(DFS_VOLUME)
        for c in pod_spec.containers:
            c.volumeMounts.append({
                'name': 'cephfs',
                'mountPath': DFS_MOUNT_DIR,
            })

        obj = Dict({
            'apiVersion': 'batch/v1',
            'kind': 'Job',
            'metadata': {
                # Unique key of the Job instance
                'name': spec.jobname,
                'labels': {
                    'kae': 'true',
                    'kae-type': 'job',
                    'kae-job-name': spec.jobname,
                },
            },
            'spec': {
                # FIXME: a workaround to forbid job controller to create too many pods
                #        see https://github.com/kubernetes/kubernetes/issues/62382
                # 'activeDeadlineSeconds': 30,
                'template': {
                    'metadata': {
                        'name': '{}-job'.format(spec.jobname),
                        'labels': {
                            'kae': 'true',
                            'kae-type': 'job',
                            'kae-job-name': spec.jobname,
                        },
                    },
                    'spec': pod_spec,
                },
            }
        })
        return obj


kube_api = KubernetesApi.instance()
