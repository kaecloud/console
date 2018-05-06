import os
import json
import base64
import copy
from addict import Dict
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import redis_lock

from console.config import USE_KUBECONFIG, HOST_VOLUMES_DIR, POD_LOG_DIR, BASE_DOMAIN, REGISTRY_AUTHS
from console.ext import rds
from .utils import parse_image_name, id_generator


def safe(f):
    def inner(self, appname, *args, **kwags):
        name = appname
        if isinstance(name, dict):
            name = name['appname']
        lock_name = "__haha_lck_{}_aaa".format(name)
        with redis_lock.Lock(rds, lock_name, expire=30, auto_renewal=True):
            return f(self, appname, *args, **kwags)

    return inner


class KubernetesApi(object):
    _INSTANCE = None

    def __init__(self, use_kubeconfig=False):
        if use_kubeconfig:
            config.load_kube_config()
        else:
            config.load_incluster_config()
        self.core_v1api = client.CoreV1Api()
        self.extensions_api = client.ExtensionsV1beta1Api()

    @classmethod
    def instance(cls, use_kubeconfig=False):
        if cls._INSTANCE is None:
            cls._INSTANCE = cls(use_kubeconfig)
        return cls._INSTANCE

    def get_app_pods(self, appname, namespace="default"):
        label_selector = "kae-app={}".format(appname)
        return self.core_v1api.list_namespaced_pod(namespace=namespace, label_selector=label_selector)

    def create_or_update_config_map(self, appname, cfg, config_name="config", namespace="default"):
        cmap = client.V1ConfigMap()
        cmap.metadata = client.V1ObjectMeta(name=appname)
        cmap.data = {config_name: cfg}
        try:
            self.core_v1api.replace_namespaced_config_map(name=appname, namespace=namespace, body=cmap)
        except ApiException as e:
            if e.status == 404:
                self.core_v1api.create_namespaced_config_map(namespace=namespace, body=cmap)
            else:
                raise e

    def get_config_map(self, appname, namespace="default"):
        result = self.core_v1api.read_namespaced_config_map(name=appname, namespace=namespace)
        return result.data

    def delete_config_map(self, appname, namespace="default"):
        self.core_v1api.delete_namespaced_config_map(name=appname, namespace=namespace, body=client.V1DeleteOptions())

    def create_or_update_secret(self, appname, secrets, namespace="default"):
        base64_secrets = {}
        for k, v in secrets.items():
            b = v
            if isinstance(b, str):
                b = v.encode("utf8")
            if not isinstance(b, bytes):
                raise ValueError("secret value should be string or dict")
            base64_secrets[k] = base64.b64encode(b).decode('utf8')
        sec = client.V1Secret()
        sec.metadata = client.V1ObjectMeta(name=appname)
        sec.type = "Opaque"
        sec.data = base64_secrets
        try:
            self.core_v1api.replace_namespaced_secret(name=appname, namespace=namespace, body=sec)
        except ApiException as e:
            if e.status == 404:
                self.core_v1api.create_namespaced_secret(namespace=namespace, body=sec)
            else:
                raise e

    def get_secret(self, appname, namespace="default"):
        result = self.core_v1api.read_namespaced_secret(name=appname, namespace=namespace)
        secrets = {}
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

    @safe
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

    @safe
    def deploy_app(self, spec, release_tag):
        deployments, services, ingress = self.create_resource_dict(spec, release_tag)
        for d in deployments:
            self.apply(d)
        for s in services:
            self.apply(s)
        for i in ingress:
            self.apply(i)

    def update_app(self, appname, spec, release_tag, namespace='default', version=None, renew_id=None):
        d = self._create_deployment_dict(spec, release_tag, version=version, renew_id=renew_id)
        self.extensions_api.replace_namespaced_deployment(name=appname, namespace=namespace, body=d)

    @safe
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

    @safe
    def delete_app(self, appname, apptype, namespace='default', ignore_404=False):
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

    def get_deployment(self, appname, namespace='default', ignore_404=False):
        """
        get kubernetes deployment object
        :param appname:
        :param namespace:
        :return:
        """
        try:
            return self.extensions_api.read_namespaced_deployment(name=appname, namespace=namespace)
        except ApiException as e:
            if e.status == 404 and ignore_404 is True:
                return None
            else:
                raise e

    @classmethod
    def create_resource_dict(cls, spec, release_tag):
        deployments = []
        services = []
        ingress = []
        apptype = spec.type

        if apptype in ("web", "worker"):
            obj = cls._create_deployment_dict(spec, release_tag)
            deployments.append(obj)

            obj = cls._create_service_dict(spec)
            services.append(obj)

            if apptype == "web":
                obj = cls._create_ingress_dict(spec)
                ingress.append(obj)
        return deployments, services, ingress

    @classmethod
    def _construct_pod_spec(cls, name, volumes_root, container_spec_list):
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
            if 'env' in container_spec:
                envs = []
                for line in container_spec['env']:
                    k, v = line.split('=')
                    envs.append({"name": k, "value": v})
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
            c.resources = {}
            if reqs:
                c.resources['requests'] = reqs
            if limits:
                c.resources['limits'] = limits

            # mount log dir
            c.volumeMounts = []
            log_mount = {
                "name": "kae-log-volumes",
                "mountPath": POD_LOG_DIR,
            }
            c.volumeMounts.append(log_mount)
            if 'volumes' in container_spec:
                for container_path in container_spec['volumes']:
                    name = container_path.replace('/', '-').strip('-')
                    name = name.replace('.', '-')
                    vol = {
                        "name": name,
                        "hostPath": {
                            "path": volumes_root + container_path,
                            "type": "DirectoryOrCreate",
                        }
                    }
                    pod_spec.volumes.append(vol)
                    volume_mount = {
                        "name": name,
                        "mountPath": container_path,
                    }
                    c.volumeMounts.append(volume_mount)

            if container_spec.configDir:
                cfg_vol = {
                    "name": "configmap-volume",
                    "configMap": {
                        "name": name
                    }
                }
                pod_spec.volumes.append(cfg_vol)
                volume_mount = {
                    "name": "configmap-volume",
                    "mountPath": container_spec.configDir,
                }
                c.volumeMounts.append(volume_mount)

            if container_spec.secrets:
                for envname, key in zip(container_spec.secrets.envNameList, container_spec.secrets.secretKeyList):
                    secret_ref = {
                        "name": envname,
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": name,
                                "key": key,
                            }
                        }
                    }
                    if 'env' not in c:
                        c.env = []
                    c.env.append(secret_ref)

        pod_spec.containers = containers
        return pod_spec

    @classmethod
    def _create_deployment_dict(cls, spec, release_tag, version=None, renew_id=None):
        appname = spec.appname
        svc = spec.service
        app_dir = os.path.join(HOST_VOLUMES_DIR, appname)
        host_kae_log_dir = os.path.join(app_dir, POD_LOG_DIR[1:])

        obj = Dict({
            'apiVersion': 'extensions/v1beta1',
            'kind': 'Deployment',
            'metadata': {
                'name': appname,
                'labels': {
                    'kae': 'true',
                    'kae-app': appname,
                },
                'annotations': {
                    'app_specs_text': json.dumps(spec),
                    'release_tag': release_tag,
                }
            },
            'spec': {
                'replicas': svc.replicas,
                'selector': {
                    'matchLabels': {
                        'kae-app': appname,
                    }
                },
                'template': {
                    'metadata': {
                        'labels': {
                            'kae': 'true',
                            'kae-app': appname,
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

        pod_spec = cls._construct_pod_spec(appname, app_dir, svc.containers)
        pod_spec.volumes.append(
            {
                "name": "kae-log-volumes",
                "hostPath": {
                    "path": host_kae_log_dir,
                    "type": "DirectoryOrCreate",
                }
            }
        )
        obj.spec.template.spec = pod_spec
        return obj

    @classmethod
    def _create_service_dict(cls, spec):
        appname = spec.appname
        svc = spec.service
        obj = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {
                'name': appname,
                'labels': {
                    'kae': 'true',
                    'kae-app': appname,
                },
            },
            'spec': {
                'selector': {
                    'kae-app': appname,
                },
                "ports": svc.ports
            }
        }
        return obj

    @classmethod
    def _create_ingress_dict(cls, spec):
        appname = spec.appname
        svc = spec.service

        obj = Dict({
            'apiVersion': 'extensions/v1beta1',
            'kind': 'Ingress',
            'metadata': {
                'name': appname,
                'labels': {
                    'kae': 'true',
                    'kae-app': appname,
                },
            },
            "spec": {
                "rules": [

                ]
            }
        })
        # parse mountpoints' host and path
        mp_cfg = {}
        for mp in svc.mountpoints:
            parts = mp.split('/', 1)
            host = parts[0]
            path = '/'
            if len(parts) == 2:
                path = '/' + parts[1]
            mp_cfg[host] = path
        default_domain = appname + '.' + BASE_DOMAIN
        if default_domain not in mp_cfg:
            mp_cfg[default_domain] = '/'

        for host, path in mp_cfg.items():
            rule = {
                'host': host,
                'http': {
                    'paths': [
                        {
                            'path': path,
                            'backend': {
                                'serviceName': appname,
                                'servicePort': 80
                            },

                        },
                    ]
                }
            }
            obj.spec.rules.append(rule)
        return obj

    @classmethod
    def _create_job_dict(cls, spec):
        obj = Dict({
            'apiVersion': 'batch/v1',
            'kind': 'Job',
            'metadata': {
                # Unique key of the Job instance
                'name': spec.jobname,
            },
            'spec': {
                'template': {
                    'metadata': {
                        'name': '{}-job'.format(spec.jobname),
                    },
                    # 'spec': { 'containers': None },
                },
            }
        })
        pod_spec = cls._construct_pod_spec(spec.jobname, None, spec.job.containers)
        obj.spec.template.spec = pod_spec
        return obj


kube_api = KubernetesApi(use_kubeconfig=USE_KUBECONFIG)
