## App Spec
每一个app会包含一个app.yaml,该文件指定了app的build和deploy的配置。

```yaml
appname: {APP_NAME}                  # global unique name
type: worker                         # web, worker and job are allowed, default is worker

builds:                             # spec for building image
- name: {NAME}:                     # image name, if it equals appname, then tag must be ignored.
  tag: {TAG}                        # image tag default is the release tag
  dockerfile: Dockerfile-alternate  # optional, default is {REPO}/Dockerfile
  target: {TARGET}                  # optional, for multi-stage build
  args:                             # optional
    buildno: 1

service:
  user: root                # default: root
  registry: xxxx            # specify different registry name
  labels:                   # labels of the container
    - proctype=router

  httpsOnly: true
  mountpoints:                 # setup domain for app
  - host: hello.k8s.gtapp.xyz
    paths:
    - /
    - /static
    tlsSecret: haha            # name of the secret which contains certification, ignore it if app needn't support https
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
  volumes: []               # a list of k8s's volume object, see https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.10/#volume-v1-core

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
    workingDir: xxx          
    gpu: 1                   # optional, only integer is allowed
    cpu:                     # cpu resource, example value: 1, 0.1, 100m
                             # if cpu isn't specified, then request is 100m
                             # and limit is 200m
      request: xxx           
      limit: xxx            
    memory:                  # memory resource, example value: 1, 1G, 1M, 1K, 1Gi, 1Mi, 1Ki
                             # if memory isn't specified, then request is 64Mi
                             # and limit is 128Mi
      request: xxx
      limit: xxx             

    ports:
      - containerPort: 9506
        protocol: TCP
        hostIP: xxx
        hostPort: xxx
        name: xxx
    volumeMounts: []       # a list of k8s's volumeMounts object, see https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.10/#volumemount-v1-core
    secrets:
      envNameList: []      # required, environment variable list
      keyList: [xxx]       # optional, if not specified, then `envNameList` is used,
                           #           every item in keyList should exist in correspond secret
    configs:
    - dir: xxxx            # required, the config map's mount path in container
      key: xxxx            # required, the key in configmap
      filename: xxx        # optional, default is the value of `key`
```
