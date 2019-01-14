# workflow
KAE的一大目标就是和gitlab ci集成，这需要一些配置

## gitlab
1. 准备一个专用账号， 为专用账号生成一个personal token
4. 为gitlab ci的runner设置环境变量 `KAE_AUTH_TOKEN` 和 `KAE_URL`,前者是上一步生
   成的token，后者是kae console的url

## 使用

    stages:
      - local test
      - kae build
      - deploy

    test:
      services:
        - docker:dind

      variables:
        DOCKER_HOST: tcp://localhost:2375
        DOCKER_DRIVER: overlay2

      stage: local test
      image: kaecloud/cli:latest
      before_script:
        - sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories
        - apk update
        - apk add --no-cache git docker
      script:
        - kae test
        - docker image ls

    build:
      stage: kae build
      image: kaecloud/cli:latest
      script:
        - kae app:register --force
        - kae app:build
      only:
        - tags



可以使用上面的模板作为 `.gitlab-ci.yml`, 可以自己添加 `deploy` stage, 同时你要在
app.yaml中添加对应的test配置, 这部分可以参考app.yaml的test部分的说明
