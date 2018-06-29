# Spec


```yaml

jobname: {JOB_NAME}
# the below 4 fields are not used by kubernetes
git: {GIT_URL}
branch: {BRANCH}          # default is master
commit: {COMMIT}          # default is latest commit id
comment: {COMMENT}        # comment of this job

backoffLimit: {XXX}       # same meaning as the kubernetes' job resource object
completions: {1}

parallelism: {1}
autoRestart: {False}

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
