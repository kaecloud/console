## prepare namespace and certifications
create two namespace `kae-app` and `kae-job`, you should add certifications to `kae-app` namespace

## prepare config.py

    GITLAB_CLIENT_ID = "gitlab app client id"
    GITLAB_CLIENT_SECRET = "gitlab app secret"

    GITLAB_HOST = 'gitlab.com'


    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://kae:123qwe@127.0.0.1:3306/kae_console?charset=utf8mb4'
    REDIS_URL = 'redis://kae-redis:6379/0'

    CLUSTER_BASE_DOMAIN_MAP = {
        "cluster1": "cluster1.kae.com",
    }

    TLS_SECRET_MAP = {
        "cluster1": {
            "domain1.com": "domain1-com-tls",
            "domain2.com": "domain2-com-tls",
        },
        "cluster2": {
            "domain1.com": "domain1-com-tls",
            "domain2.com": "domain2-com-tls",
        },
    }

    DFS_HOST_DIR_MAP = {
        "cluster-name": "/dfs-root-path-in-host",
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
