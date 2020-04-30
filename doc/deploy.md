## prepare namespace and certifications
create two namespace `kae-app` and `kae-job`, you should add certifications to `kae-app` namespace

## prepare config.py

    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://kae:123qwe@127.0.0.1:3306/kae_console?charset=utf8mb4'
    REDIS_URL = 'redis://kae-redis:6379/0'
    
    CLUSTER_CFG = {
        "cluster1": {
            "k8s": "k8s name",
            "namespace": "",
            # optional, cluster's dfs root directory
            "dfs_host_dir": "/dfs-root-path-in-host",
            # Set base domain for cluster, when a cluster has base domain,
            # every app in that cluster will a host name `appname.basedomain`
            # if you use incluster config, then the cluster name should be `incluster`.
            "base_domain": "cluster1.kae.com",
            "tls_secrets": {
                "domain1.com": "domain1-com-tls",
                "domain2.com": "domain2-com-tls",
            }
        },
        "cluster2": {
            "k8s": "k8s name",
            "namespace": "",
        }
    }
    
    EMAIL_DOMAIN = 'email.com'
    EMAIL_SENDER = "notifications@email.com"
    EMAIL_SENDER_PASSWOORD = "password"

    BOT_WEBHOOK_URL = 'slack or bearychat webhool url'

    SENTRY_DSN = "xxxx"

## prepare mysql schema
set `SQLALCHEMY_DATABASE_URI` correctly, run `shell.py`, then execute `db.create_all()`

## create secret
  you need to create secret for git and docker

    kubectl create secret generic kae-console --from-file=id_rsa=config/id_rsa --from-file=docker_config.json=config/docker_config.json --from-file=config.py=config/config.py --namespace kae

  you can also privide an optional kubeconfig

    kubectl create secret generic kae-console --from-file=kubeconfig=config/kubeconfig --from-file=id_rsa=config/id_rsa --from-file=docker_config.json=config/docker_config.json --from-file=config.py=config/config.py --namespace kae

## prepare pullImageSecret
 in order to pull image from private repository, you need to create secret in every namespace.

    kubectl create secret docker-registry aliyun --docker-server=registry.cn-hangzhou.aliyuncs.com --docker-username=xxxx --docker-password=xxxxx --docker-email=xxx@xxx.com

## deploy to kubernetes

    helm upgrade kae-console deploy/kae-console
