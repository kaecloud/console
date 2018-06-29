## App Spec
每一个app会包含一个app.yaml,该文件指定了app的build和deploy的配置。

```yaml
appname: {APP_NAME}                  # global unique name
type: worker                         # web, worker and job are allowed, default is worker
git: {GIT_URL}                       # required, remote git repository url

builds:                             # spec for building image
- name: {NAME}:                     # image name, if it equal appname, then tag must be ignored.
  tag: {TAG}                        # image tag default is the release tag
  dockerfile: Dockerfile-alternate  # optional, default is {REPO}/Dockerfile
  target: {TARGET}                  # optional, for multi-stage build
  args:                             # optional
    buildno: 1

service:
  user: root                # default: root
  labels:                   # labels of the container
    - proctype=router
  
  mountpoints:             # 只支持 Type==web 的 app(默认已分配 ${appname}.${domain} 的域名)
  - hello.k8s.gtapp.xyz    # 响应 a.external.domain1 这个外网域名 b/c 段 location 的请求
  ports:                     # ports exposed in kubernetes service
  - port: 80           # service port (required)
    targetPort: 8080   # container port, equal to port if not specified
    protocol: TCP      # TCP and UDP are allowed, default is TCP

  minReadySeconds: 0
  progressDeadlineSeconds: 600
  replicas: 1               # default: 1
  strategy:                 # update strategy
    type: RollingUpdate     # RollingUpdate or Recreate is allowed
    rollingUpdate:          # only valid when type is RollingUpdate
      maxSurge: 25%
      maxUnavailable: 25%
      
  containers:
  - name: "xxx"
    image: {IMAGE}           # image of the container
    imagePullPolicy: xxx     # One of Always, Never, IfNotPresent. Defaults to Always if :latest tag is specified, or IfNotPresent otherwise. Cannot be updated
    args: ["xx", "xx"]       # Arguments to the entrypoint. if not specified, the image's CMD is used
    command: ['xx', 'xx']    # entrypoint array, if not specified, the image's ENTRYPOINT is used

    env:                     # environments
      - ENVA=a
      - ENVB=b
    tty: false               # whether allocate tty
    workingDir: xxx          # working dir
    cpu:
      request: xxx
      limit: xxx
    memory:
      request: xxx
      limit: xxx

    ports:
      - containerPort: 9506
        protocol: TCP
        hostIP: xxx
        hostPort: xxx
        name: xxx
    volumes:                      # volume files
      - /var/log
      - /etc/nginx/nginx.conf
    dfsVolumes:
      - /data
    secrets:
      envNameList: []          # envNameList and secretKeyList are one to one mapped
      secretKeyList: []
    configDir: xxxxx       # the config map's mount path in container
```
