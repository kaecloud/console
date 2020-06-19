import os
import time
import yaml
import base64
import copy
import json
from addict import Dict
from kubernetes import client, config, watch
from kubernetes.watch.watch import iter_resp_lines
from kubernetes.stream import stream

from kubernetes.client.rest import ApiException

from console.config import (
    HOST_VOLUMES_DIR, POD_LOG_DIR,
    REGISTRY_AUTHS, INGRESS_ANNOTATIONS_PREFIX, CLUSTER_CFG,
)

from .utils import (
    parse_image_name, id_generator, make_canary_appname, search_tls_secret, get_dfs_host_dir,
)

ANNO_CONFIG_ID = "kae-app-config-id"
ANNO_DEPLOY_INFO = "kae-app-deploy-info"

# TODO: when the upstream upgrade, remove these dirty code
# fix the stupid Condition is None problem,
# see: https://github.com/kubernetes-client/python/issues/1098
import wrapt
def fix_V2beta2HorizontalPodAutoscalerStatus___init__(wrapped, instance, args, kwargs):
    def _resolve(conditions=None, current_metrics=None, current_replicas=None, desired_replicas=None, last_scale_time=None, observed_generation=None):  # noqa: E501
        return {
            "conditions": conditions or [],
            "current_metrics": current_metrics,
            "current_replicas": current_replicas,
            "desired_replicas": desired_replicas,
            "last_scale_time": last_scale_time,
            "observed_generation": observed_generation,
        }
    final_kwargs = _resolve(*args, **kwargs)

    return wrapped(**final_kwargs)


wrapt.wrap_function_wrapper(
        'kubernetes.client.models.v2beta2_horizontal_pod_autoscaler_status',
        'V2beta2HorizontalPodAutoscalerStatus.__init__',
        fix_V2beta2HorizontalPodAutoscalerStatus___init__)


class KubeError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg


class K8sNotExistError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg


class KubeApi(object):
    _INSTANCE = None
    ALL_CLUSTER = "__all_cluster__"

    def __init__(self):
        self.k8s_api_map = {}
        self.kae_cluster_map = {}

    @classmethod
    def instance(cls):
        if cls._INSTANCE is None:
            cls._INSTANCE = cls()
        return cls._INSTANCE

    @property
    def cluster_names(self):
        return list(CLUSTER_CFG.keys())

    def cluster_exist(self, cluster_name):
        return cluster_name in CLUSTER_CFG

    def _load_k8s_client(self, name):
        if name not in self.k8s_api_map:
            # get k8s clusters
            if os.path.exists(os.path.expanduser("~/.kube/config")):
                contexts, active_context = config.list_kube_config_contexts()
                if not contexts:
                    raise Exception("no context in kubeconfig")

                ctx_names = [context['name'] for context in contexts]
                if name not in ctx_names:
                    raise K8sNotExistError(f"k8s context {name} not exist")

                api_map = {}
                api_map['core_v1_api'] = client.CoreV1Api(
                    api_client=config.new_client_from_config(context=name))
                api_map['apps_v1_api'] = client.AppsV1Api(
                    api_client=config.new_client_from_config(context=name))
                api_map['extensions_v1beta1_api'] = client.ExtensionsV1beta1Api(
                    api_client=config.new_client_from_config(context=name))
                api_map['scale_v2beta2_api'] = client.AutoscalingV2beta2Api(
                    api_client=config.new_client_from_config(context=name))
                self.k8s_api_map[name] = api_map
            else:
                if name != "incluster":
                    raise K8sNotExistError(f"k8s context {name} not exist")
                config.load_incluster_config()
                api_map = {
                    'core_v1_api': client.CoreV1Api(),
                    'apps_v1_api': client.AppsV1Api(),
                    'extensions_v1beta1_api': client.ExtensionsV1beta1Api(),
                    'scale_v2beta2_api': client.AutoscalingV2beta2Api(),
                }
                self.k8s_api_map['incluster'] = api_map
        return self.k8s_api_map[name]

    def _load_kae_cluster(self, name):
        if name not in self.kae_cluster_map:
            cluster_info = CLUSTER_CFG[name]
            k8s_name = cluster_info["k8s"]
            k8s_namespace = cluster_info["namespace"]
            try:
                k8s_cluster = self._load_k8s_client(k8s_name)
                self.kae_cluster_map[name] = KaeCluster(name, k8s_namespace, **k8s_cluster)
            except K8sNotExistError:
                raise KubeError(f"kae cluster {name}'s k8s {k8s_name} doesn't exist")
        return self.kae_cluster_map[name]

    def __getattr__(self, item):
        def wrapper(*args, **kwargs):
            def _exec_on_single_cluster(name):
                kae_cluster = self._load_kae_cluster(name)
                func = getattr(kae_cluster, item)
                return func(*args, **kwargs)

            try:
                cluster_name = kwargs.pop('cluster_name')
            except KeyError:
                raise ValueError("cluster_name is needed")

            if cluster_name == self.ALL_CLUSTER:
                results = {}
                for name, cluster_info in CLUSTER_CFG.items():
                    results[name] = _exec_on_single_cluster(name)
                return results

            cluster_info = CLUSTER_CFG.get(cluster_name, None)
            if cluster_info is None:
                raise Exception("cluster {} is not available".format(cluster_name))
            return _exec_on_single_cluster(cluster_name)
        return wrapper


class KaeCluster(object):
    def __init__(self, name, namespace, **api_map):
        self.name = name
        self.cluster = name
        self.namespace = namespace
        self.api_map = api_map

    @property
    def core_api(self):
        return self.api_map["core_v1_api"]

    @property
    def apps_api(self):
        return self.api_map["apps_v1_api"]

    @property
    def scale_api(self):
        return self.api_map["scale_v2beta2_api"]

    @property
    def extensions_api(self):
        return self.api_map['extensions_v1beta1_api']

    def get_pods(self, label_selector):
        return self.core_api.list_namespaced_pod(namespace=self.namespace, label_selector=label_selector)

    def get_pod_log(self, podname, **kwargs):
        kwargs.pop('follow', False)
        return self.core_api.read_namespaced_pod_log(name=podname, namespace=self.namespace, **kwargs)

    def follow_pod_log(self, podname, **kwargs):
        kwargs['_preload_content'] = False
        kwargs['follow'] = True
        resp = self.core_api.read_namespaced_pod_log(name=podname, namespace=self.namespace, **kwargs)
        for line in iter_resp_lines(resp):
            yield line

    def get_app_pods(self, name):
        label_selector = "kae-app-name={}".format(name)
        return self.core_api.list_namespaced_pod(namespace=self.namespace, label_selector=label_selector)

    def watch_pods(self, label_selector=None, **kwargs):
        if label_selector is None:
            label_selector = "kae=true"
        w = watch.Watch()
        return w.stream(self.core_api.list_pod_for_all_namespaces, label_selector=label_selector, **kwargs)

    def exec_shell(self, podname, container=None):
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
        resp = stream(self.core_api.connect_get_namespaced_pod_exec, podname, self.namespace, **kwargs)
        return resp

    def stop_container(self, podname, container=None):
        exec_command = ['/bin/kill', '1']
        kwargs = {
            "command": exec_command,
            "stdin": False,
            "tty": False,
            "stderr": True,
            "stdout": True,
            "_preload_content": False,
        }
        if container:
            kwargs['container'] = container
        resp = stream(self.core_api.connect_get_namespaced_pod_exec, podname, self.namespace, **kwargs)
        return resp

    def get_hpa(self, appname, ignore_404=False):
        try:
            return self.scale_api.read_namespaced_horizontal_pod_autoscaler(appname, namespace=self.namespace)
        except ApiException as e:
            if e.status == 404 and ignore_404 is True:
                return None
            else:
                raise e

    def create_hpa(self, appname, hpa_data):
        """
        Create horizontal pod autoscaler
        see: https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/V2beta2HorizontalPodAutoscaler.md
        """
        obj = Dict({
            "apiVersion": "autoscaling/v2beta2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": appname,
            },
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": appname,
                },
                "maxReplicas": hpa_data['maxReplicas'],
                "minReplicas": hpa_data['minReplicas'],
                "metrics": [],
            }
        })
        metrics = []
        for metric_spec in hpa_data['metrics']:
            if 'averageUtilization' in metric_spec:
                target = client.V2beta2MetricTarget(
                    type='Utilization',
                    average_utilization=metric_spec['averageUtilization'],
                )
            elif 'averageValue' in metric_spec:
                target = client.V2beta2MetricTarget(
                    type='AverageValue',
                    average_value=metric_spec['averageValue'],
                )
            else:
                target = client.V2beta2MetricTarget(
                    type='Value',
                    average_utilization=metric_spec['value'],
                )

            resource = client.V2beta2ResourceMetricSource(
                name=metric_spec['name'],
                target=target,
            )
            metric = client.V2beta2MetricSpec(
                type="Resource",
                resource=resource,
            )
            metrics.append(metric)
        obj["spec"]["metrics"] = metrics

        try:
            self.scale_api.replace_namespaced_horizontal_pod_autoscaler(name=appname, namespace=self.namespace, body=obj)
        except ApiException as e:
            if e.status == 404:
                self.scale_api.create_namespaced_horizontal_pod_autoscaler(namespace=self.namespace, body=obj)
            else:
                raise e

    def delete_hpa(self, appname, ignore_404=False):
        try:
            self.scale_api.delete_namespaced_horizontal_pod_autoscaler(name=appname, namespace=self.namespace)
        except ApiException as e:
            if not (e.status == 404 and ignore_404 is True):
                raise e

    def create_or_update_config_map(self, appname, config_id, cm_data):
        """
        create or update configmap for specfied app
        :param appname:
        :param cm_data: configmap data dict
        :return:
        """
        obj = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": appname,
                "annotations": {
                    ANNO_CONFIG_ID: str(config_id),
                }
            },
            "data": cm_data,
        }
        try:
            self.core_api.replace_namespaced_config_map(name=appname, namespace=self.namespace, body=obj)
        except ApiException as e:
            if e.status == 404:
                self.core_api.create_namespaced_config_map(namespace=self.namespace, body=obj)
            else:
                raise e

    def get_config_map(self, appname, raw=False, ignore_404=False):
        """
        get configmap of specfied app
        :param appname: app name
        :param raw: if set True return the raw configmap object, otherwise return the data in configmap
        :return:
        """
        try:
            result = self.core_api.read_namespaced_config_map(name=appname, namespace=self.namespace)
            if raw:
                return result 
            else:
                return result.data
        except ApiException as e:
            if e.status == 404 and ignore_404 is True:
                return None
            else:
                raise e

    def delete_config_map(self, appname):
        self.core_api.delete_namespaced_config_map(name=appname, namespace=self.namespace, body=client.V1DeleteOptions())

    def create_or_update_secret(self, appname, secrets, replace=True):
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
            sec = self.core_api.read_namespaced_secret(name=appname, namespace=self.namespace)
            if replace:
                sec.data = base64_secrets
            else:
                sec.data.update(base64_secrets)
            self.core_api.replace_namespaced_secret(name=appname, namespace=self.namespace, body=sec)
        except ApiException as e:
            if e.status == 404:
                sec = client.V1Secret()
                sec.metadata = client.V1ObjectMeta(name=appname)
                sec.type = "Opaque"
                sec.data = base64_secrets
                self.core_api.create_namespaced_secret(namespace=self.namespace, body=sec)
            else:
                raise e

    def get_secret(self, appname):
        result = self.core_api.read_namespaced_secret(name=appname, namespace=self.namespace)
        secrets = {}
        if not result.data:
            return secrets

        for k, base64_v in result.data.items():
            v = base64.b64decode(base64_v).decode('utf8')
            secrets[k] = v

        return secrets

    def delete_secret(self, appname):
        self.core_api.delete_namespaced_secret(name=appname, namespace=self.namespace, body=client.V1DeleteOptions())

    def apply(self, d):
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
                self.apps_api.replace_namespaced_deployment(name=name, body=d, namespace=self.namespace)
            except ApiException as e:
                if e.status == 404:
                    self.apps_api.create_namespaced_deployment(body=d, namespace=self.namespace)
                else:
                    raise e
        elif kind == "Service":
            self.create_or_update_service(d)
        elif kind == "Ingress":
            try:
                self.extensions_api.replace_namespaced_ingress(name=name, body=d, namespace=self.namespace)
            except ApiException as e:
                if e.status == 404:
                    self.extensions_api.create_namespaced_ingress(body=d, namespace=self.namespace)
                else:
                    raise e

    def create_or_update_service(self, d):
        name = d['metadata']['name']
        try:
            result = self.core_api.read_namespaced_service(name=name, namespace=self.namespace)
            d['metadata']['resourceVersion'] = result.metadata.resource_version
            d['spec']['clusterIP'] = result.spec.cluster_ip
            self.core_api.replace_namespaced_service(name=name, body=d, namespace=self.namespace)
        except ApiException as e:
            if e.status == 404:
                self.core_api.create_namespaced_service(body=d, namespace=self.namespace)
            else:
                raise e

    def scale_app(self, appname, replicas):
        obj = {
            "spec": {
                "replicas": replicas,
            }
        }
        return self.apps_api.patch_namespaced_deployment(appname, body=obj, namespace=self.namespace)

    def renew_app(self, appname):
        """
        force kubernetes to recreate the pods, it mainly used to make secrets and configmap effective.
        :param appname:
        :param namespace:
        :return:
        """
        deployment = self.apps_api.read_namespaced_deployment(name=appname, namespace=self.namespace)

        if deployment.spec.template.metadata.annotations is None:
            deployment.spec.template.metadata.annotations = {}
        deployment.spec.template.metadata.annotations['renew_id'] = id_generator(10)
        self.apps_api.replace_namespaced_deployment(name=appname, namespace=self.namespace, body=deployment)

    def deploy_app(self, spec, deploy_ver, ignore_config=False):
        ing = None
        dp_annotations = {
            ANNO_DEPLOY_INFO: json.dumps(deploy_ver.to_k8s_annotation()),
        }
        # step 1: create configmap if neccessary
        app_cfg = deploy_ver.app_config
        if app_cfg is not None and ignore_config is False:
            self.create_or_update_config_map(app_cfg.appname, app_cfg.id, app_cfg.data_dict)

        dp = self._create_deployment_dict(spec, annotations=dp_annotations)
        svc = self._create_service_dict(spec)

        if spec.type == "web":
            ing = self._create_ingress_dict(spec)
        self.apply(dp)
        self.apply(svc)
        if ing is not None:
            self.apply(ing)
        # create HPA if neccessary
        hpa_data = spec.service.hpa
        if hpa_data:
            self.create_hpa(spec.appname, hpa_data)
        else:
            # delete any exist HPA
            self.delete_hpa(spec.appname, ignore_404=True)

    def deploy_app_canary(self, spec, release_tag, app_cfg=None, ignore_config=False):
        """
        create Canary Deployment for specified app.
        """
        spec_copy = copy.deepcopy(spec)
        canary_appname = make_canary_appname(spec['appname'])

        if app_cfg is not None and ignore_config is False:
            self.create_or_update_config_map(canary_appname, app_cfg.id, app_cfg.data_dict)
        dp_annotations = {
            'spec': yaml.dump(spec_copy.to_dict()),
            'release_tag': release_tag,
        }
        dp_dict = self._create_deployment_dict(spec_copy, annotations=dp_annotations, canary=True)
        svc_dict = self._create_service_dict(spec_copy, canary=True)

        self.apply(dp_dict)
        self.apply(svc_dict)

    def add_canary_backend(self, appname, ing):
        canary_appname = make_canary_appname(appname)
        for rule in ing.spec.rules:
            found = False
            extra_paths = []
            for path in rule.http.paths:
                if path.backend.service_name == canary_appname:
                    found = True
                    break
            if found is False:
                canary_path = copy.deepcopy(rule.http.paths[0])
                canary_path.backend.service_name = canary_appname
                extra_paths.append(canary_path)
            rule.http.paths.extend(extra_paths)
        return ing

    def set_abtesting_rules(self, appname, rules):
        weight_keys = [
            "traefik.ingress.kubernetes.io/service-weights",
            "{}/service-weight".format(INGRESS_ANNOTATIONS_PREFIX)
        ]
        canary_appname = make_canary_appname(appname)
        annotations_key = "{}/service-match".format(INGRESS_ANNOTATIONS_PREFIX)
        ing = self.extensions_api.read_namespaced_ingress(appname, namespace=self.namespace)
        # data = {
        #     "backend": {
        #         "service": canary_appname,
        #         # for web app, the service port is 80
        #         "port": 80,
        #     },
        #     "rules": rules,
        # }
        data = "{}:{}\n".format(canary_appname, rules)
        annotations = ing.metadata.annotations if ing.metadata.annotations else {}
        annotations[annotations_key] = data
        for weight_key in weight_keys:
            if weight_key in annotations:
                annotations.pop(weight_key)

        ing.metadata.annotations = annotations
        # add backend if needed
        ing = self.add_canary_backend(appname, ing)

        self.extensions_api.replace_namespaced_ingress(name=appname, body=ing, namespace=self.namespace)

    def get_abtesting_rules(self, appname):
        annotations_key = "{}/service-match".format(INGRESS_ANNOTATIONS_PREFIX)
        ing = self.extensions_api.read_namespaced_ingress(appname, namespace=self.namespace)
        annotations = ing.metadata.annotations if ing.metadata.annotations else {}
        full_rules_str = annotations.get(annotations_key, None)
        if full_rules_str is None:
            return None
        # full_rules = json.loads(full_rules_str)
        parts = full_rules_str.split(":", 1)
        if len(parts) < 2:
            return None
        return parts[1]
        # return full_rules.get("rules", None)

    def set_canary_weight(self, appname, weight):
        canary_appname = make_canary_appname(appname)
        annotations_key = "traefik.ingress.kubernetes.io/service-weights"
        annotations_val = "{}: {}%\n".format(canary_appname, weight)
        ngx_annotations_key = "{}/service-weight".format(INGRESS_ANNOTATIONS_PREFIX)
        ngx_match_key = "{}/service-match".format(INGRESS_ANNOTATIONS_PREFIX)
        # <new-svc-name>:<new-svc-weight>, <old-svc-name>:<old-svc-weight>
        ngx_annotations_val = "{}:{}, {}:{}\n".format(canary_appname, weight, appname, 100-weight)

        ing = self.extensions_api.read_namespaced_ingress(appname, namespace=self.namespace)
        annotations = ing.metadata.annotations if ing.metadata.annotations else {}
        annotations[annotations_key] = annotations_val
        annotations[ngx_annotations_key] = ngx_annotations_val
        if ngx_match_key in annotations:
            annotations.pop(ngx_match_key)
        ing.metadata.annotations = annotations

        # add canary backend if needed
        ing = self.add_canary_backend(appname, ing)

        self.extensions_api.replace_namespaced_ingress(name=appname, body=ing, namespace=self.namespace)

    def undeploy_app_canary(self, appname):
        canary_appname = make_canary_appname(appname)
        delete_keys = [
            "{}/service-match".format(INGRESS_ANNOTATIONS_PREFIX),
            "{}/service-weight".format(INGRESS_ANNOTATIONS_PREFIX),
            "traefik.ingress.kubernetes.io/service-weights",
        ]
        # remove abtesting rules
        try:
            ing = self.extensions_api.read_namespaced_ingress(appname, namespace=self.namespace)

            annotations = ing.metadata.annotations if ing.metadata.annotations else {}
            for k in delete_keys:
                if k in annotations:
                    ing.metadata.annotations.pop(k)
            for rule in ing.spec.rules:
                need_delete = []
                for path in rule.http.paths:
                    if path.backend.service_name == canary_appname:
                        need_delete.append(path)
                for path in need_delete:
                    rule.http.paths.remove(path)

            self.extensions_api.replace_namespaced_ingress(name=appname, body=ing, namespace=self.namespace)
            # the nginx-ingress needs about 1 seconds to detect the change of the ingress
            time.sleep(1)
        except ApiException as e:
            if e.status != 404:
                raise e
        # remove sevice
        try:
            self.core_api.delete_namespaced_service(
                name=canary_appname, namespace=self.namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground",
                                            grace_period_seconds=5))
        except ApiException as e:
            if e.status != 404:
                raise e
        # remove deployment
        try:
            self.apps_api.delete_namespaced_deployment(
                name=canary_appname, namespace=self.namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground",
                                            grace_period_seconds=5))
        except ApiException as e:
            if e.status != 404:
                raise e
        try:
            self.delete_config_map(canary_appname)
        except ApiException as e:
            if e.status != 404:
                raise e

    def rollback_app(self, appname, spec, deploy_ver, version=None):
        dp_annotations = {
            ANNO_DEPLOY_INFO: json.dumps(deploy_ver.to_k8s_annotation()),
        }
        # change configmap if necessary
        app_cfg = deploy_ver.app_config
        if app_cfg is not None:
            self.create_or_update_config_map(app_cfg.appname, app_cfg.id, app_cfg.data_dict)
        # replace deployment
        d = self._create_deployment_dict(spec, version=version, annotations=dp_annotations)
        self.apps_api.replace_namespaced_deployment(name=appname, namespace=self.namespace, body=d)
        # create HPA if neccessary
        hpa_data = spec.service.hpa
        if hpa_data:
            self.create_hpa(spec.appname, hpa_data)
        else:
            # delete any exist HPA
            self.delete_hpa(spec.appname, ignore_404=True)

    def undeploy_app(self, appname, apptype, ignore_404=False):
        self.undeploy_app_canary(appname)

        # delete resource in the following order: ingress, service, hpa, deployment, secret, configmap
        if apptype == "web":
            try:
                self.extensions_api.delete_namespaced_ingress(
                    name=appname, namespace=self.namespace,
                    body=client.V1DeleteOptions(propagation_policy="Foreground",
                                                grace_period_seconds=5))
            except ApiException as e:
                if not (e.status == 404 and ignore_404 is True):
                    raise e

        if apptype in ("worker", "web"):
            try:
                self.core_api.delete_namespaced_service(
                    name=appname, namespace=self.namespace,
                    body=client.V1DeleteOptions(propagation_policy="Foreground",
                                                grace_period_seconds=5))
            except ApiException as e:
                if not (e.status == 404 and ignore_404 is True):
                    raise e
        try:
            self.delete_hpa(appname)
        except ApiException as e:
            if e.status != 404:
                raise e
        try:
            self.apps_api.delete_namespaced_deployment(
                name=appname, namespace=self.namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground",
                                            grace_period_seconds=5))
        except ApiException as e:
            if not (e.status == 404 and ignore_404 is True):
                raise e

        try:
            self.delete_secret(appname)
        except ApiException as e:
            if e.status != 404:
                raise e
        try:
            self.delete_config_map(appname)
        except ApiException as e:
            if e.status != 404:
                raise e

    def get_deployment(self, name, ignore_404=False):
        """
        get kubernetes deployment object
        :param name:
        :param namespace:
        :return:
        """
        try:
            return self.apps_api.read_namespaced_deployment(name=name, namespace=self.namespace)
        except ApiException as e:
            if e.status == 404 and ignore_404 is True:
                return None
            else:
                raise e

    def get_ingress(self, name, ignore_404=False):
        """
        get kubernetes deployment object
        :param name:
        :param namespace:
        :return:
        """
        try:
            return self.extensions_api.read_namespaced_ingress(name=name, namespace=self.namespace)
        except ApiException as e:
            if e.status == 404 and ignore_404 is True:
                return None
            else:
                raise e

    def _construct_pod_spec(self, name, volumes_root, container_spec_list,
                            restart_policy='Always', initial_env=None, host_aliases=None,
                            initial_vol_mounts=None, default_work_dir=None, secret_name=None, config_name=None):
        use_dfs = False
        cluster_dfs_exists = (get_dfs_host_dir(self.cluster) is not None)
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

        if host_aliases:
            pod_spec.hostAliases = host_aliases

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
            if container_spec.useDFS and cluster_dfs_exists:
                use_dfs = True
                volume_mount = {
                    "name": "dfs-volume",
                    "mountPath": "/kae/dfs",
                }
                c.volumeMounts.append(volume_mount)

        if has_configmap:
            cfg_vol = {
                "name": "configmap-volume",
                "configMap": {
                    "name": config_name,
                }
            }
            pod_spec.volumes.append(cfg_vol)

        if use_dfs and cluster_dfs_exists:
            dfs_host_dir = get_dfs_host_dir(self.cluster)
            dfs_vol = {
                "name": "dfs-volume",
                "hostPath": {
                    "path": os.path.join(dfs_host_dir, "kae/apps", name),
                    "type": "DirectoryOrCreate",
                }
            }
            pod_spec.volumes.append(dfs_vol)

        pod_spec.containers = containers
        pod_spec.restartPolicy = restart_policy
        return pod_spec

    def _create_deployment_dict(self, spec, version=None, annotations=None, canary=False):
        if canary:
            appname = make_canary_appname(spec['appname'])
        else:
            appname = spec.appname
        # secret_name is the secret name and configmap name for this app
        secret_name = spec.appname
        config_name = appname

        svc = spec.service
        app_dir = os.path.join(HOST_VOLUMES_DIR, appname)
        host_kae_log_dir = os.path.join(app_dir, POD_LOG_DIR[1:])

        if annotations is None:
            annotations = {}

        obj = Dict({
            'apiVersion': 'apps/v1',
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

        if 'hostAliases' in svc:
            hostAliases = svc.hostAliases
        else:
            hostAliases = None

        log_mount = {
            "name": "kae-log-volumes",
            "mountPath": POD_LOG_DIR,
        }
        pod_spec = self._construct_pod_spec(appname, app_dir, svc.containers, host_aliases=hostAliases,
                                            initial_vol_mounts=[log_mount], secret_name=secret_name,
                                            config_name=config_name)
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

    def _create_ingress_dict(self, spec):
        cluster = self.cluster
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

        cluster_domain = CLUSTER_CFG[cluster].get("base_domain", None)
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

