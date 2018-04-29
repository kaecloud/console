from box import Box
from kubernetes import client, config, watch


class KubernetesApi(object):
    def __init__(self, use_kubeconfig=False):
        if use_kubeconfig:
            config.load_kube_config()
        else:
            config.load_incluster_config()
        self.core_v1api = client.CoreV1Api()
        self.extensions_api = client.ExtensionsV1beta1Api()

    def get_pods(self, appname, svc_name):
        self.core_v1api.list_pod_for_all_namespaces()

    def apply(self, d, namespace="default"):
        kind = d["kind"]
        if kind == "Deployment":
            self.extensions_api.create_namespaced_deployment(body=d, namespace=namespace)
        elif kind == "Service":
            self.core_v1api.create_namespaced_service(body=d, namespace=namespace)
        elif kind == "Ingress":
            self.extensions_api.create_namespaced_ingress(body=d, namespace=namespace)

    def delete_app_service(self, appname, svc_name, svc, namespace):
        full_name = "{}.{}".format(appname, svc_name)
        self.extensions_api.delete_namespaced_deployment(
            name=full_name, namespace=namespace,
            body=client.V1DeleteOptions(propagation_policy="Foreground",
                                        grace_period_seconds=5))
        if svc.type in ("worker", "web"):
            self.core_v1api.delete_namespaced_service(
                name=full_name, namespace=namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground",
                                            grace_period_seconds=5))
        if svc.type == "web":
            self.extensions_api.delete_namespaced_ingress(
                name=full_name, namespace=namespace,
                body=client.V1DeleteOptions(propagation_policy="Foreground",
                                            grace_period_seconds=5))

    @classmethod
    def create_resource_dict(cls, spec):
        deployments = []
        services = []
        ingress = []
        for name, svc in spec.services.items():
            if svc.type in ("web", "worker"):
                obj = cls._create_deployment_dict(spec.appname, name, svc)
                deployments.append(obj)

                obj = cls._create_service_dict(spec.appname, name, svc)
                services.append(obj)

                if svc.type == "web":
                    obj = cls._create_ingress_dict(spec.appname, name, svc)
                    ingress.append(obj)
        return deployments, services, ingress

    @classmethod
    def _create_deployment_dict(cls, appname, svc_name, svc):
        obj = {
            'apiVersion': 'extensions/v1beta1',
            'kind': 'Deployment',
            'metadata': {
                'name': '{}.{}'.format(appname, svc_name),
                'labels': {
                    'app': appname,
                    'service': svc_name,
                },
            },
            'spec': {
                'replicas': svc.replicas,
                'selector': {
                    'matchLabels': {
                        'app': appname,
                        'service': svc_name,
                    }
                },
                'template': {
                    'metadata': {
                        'labels': {
                            'app': appname,
                            'service': svc_name,
                        }
                    },
                    'spec': {
                        'containers': svc.containers,
                    }
                }
            }
        }
        return obj

    @classmethod
    def _create_service_dict(cls, appname, svc_name, svc):
        obj = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {
                'name': '{}.{}'.format(appname, svc_name),
            },
            'spec': {
                'selector': {
                    'app': appname,
                    'service': svc_name,
                },
                "ports": [
                ]
            }
        }
        for p in svc.ports:
            parts = p.split("/")
            dd = {
                "targetPort": parts[0],
            }
            if len(parts) == 2:
                dd['port'] = parts[1]
            if len(parts) == 3:
                dd['protocol'] = parts[2]
            obj['spec']['ports'].append(dd)
        return obj

    @classmethod
    def _create_ingress_dict(cls, appname, svc_name, svc):
        obj = Box({
            'apiVersion': 'extensions/v1beta1',
            'kind': 'Ingress',
            'metadata': {
                'name': '{}.{}-ingress'.format(appname, svc_name),
            },
            "spec": {
                "rules": [

                ]
            }
        })
        for mp in svc.mountpoints:
            parts = mp.split('/', 1)
            rule = {
                'host': parts[0],
                'http': {
                    'paths': [
                        {
                            'path': '/foo',
                            'backend': {
                                'serviceName': 's1',
                                'servicePort': 80
                            },

                        },
                    ]
                }
            }
            obj.spec.rules.append(rule)
        return obj
