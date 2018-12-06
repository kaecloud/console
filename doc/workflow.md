# workflow
KAE的一大目标就是和gitlab和gitlab ci集成，这需要一些配置

## gitlab
1. 所有人都要有读权限（方便交流，特殊项目除外）
2. 准备一个专用账号，并准备对应的公钥和私钥，该账号必须对所有需要部署的项目有读权限
3. 为专用账号生成一个Application，得到对应的key
4. 为gitlab ci的runner设置环境变量 `KAE_AUTH_TOKEN` 和 `KAE_URL`,前者是第三步生成的key，后者是kae console的url

## 使用

    stages:
      - build
      - test
      - deploy

    build:
      stage: build
      image: yuyang0/kae-cli:latest
      script:
        # Compile and name the binary as `hello`
        - kae app:register --force
        - kae app:build
      only:
        - tags

可以使用上面的模板作为 `.gitlab-ci.yml`, 可以添加 `test`和 `deploy` stage
