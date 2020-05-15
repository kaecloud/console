# KAE的RBAC权限系统
##　简介
RBAC是当前比较流行的权限系统,基本的设计思路是增加role这样一个中间层,用户或者组和role关联,然后role和
具体的权限关联, 这样可以让权限控制更灵活

## KAE的实现
###　Role
Role的实现基本类似与下面的示例数据

    {
        "name": "role-name",
        "apps": ["app1", "app2"],
        "actions": ["get", "update", "delete", "enter_container"],
        "clusters": ["cluser1", "cluster2"]
    }
    
+ name: role的名字
+ apps: 该role能操作的app列表
+ actions: 该role能执行的操作, 不能为空
+ clusters: 该role能操作的cluster列表,如果为空则可以操作所有集群

#### action list
当前KAE支持的action列表如下:


+ get: 和读相关的都需要该权限, 比如读app, release, deployments等等(config和secret例外,因为他们包含敏感信息,所以需要单独的GET_CONFIG和GET_SECRET)
+ update: 更新一些app相关的对象需要该权限,比如更新app yaml
+ delete: 删除一些app相关的对象需要该权限,比如删除app yaml
+ build: 执行build_app需要该权限
+ get_config: 读取config
+ update_config: 更新config
+ get_secret: 获取secret
+ update_secret: 更新secret
+ deploy: deploy_app 需要该权限
+ undeploy: undeploy_app需要改权限
+ renew: renew_app 需要该权限
+ rollback: rollback_app需要该权限
+ stop_container: 重启容器时需要该权限
+ enter_container: 进入容器需要该权限

+ admin: app管理员,可以对apps中的app执行任何操作,包括删除app
+ kae_admin: 系统管理员权限,可以执行任何操作, 特别注意,进入admin后台需要该权限

### user role binding
将单个user和某个role绑定, 绑定后该user就拥有了role的权限

### group role binding
将单个group和role绑定, 绑定后该group中的所有user就拥有了role的权限

## 创建app时的默认行为
为了简化使用, 当一个用户创建新的app时, kae会生成一些默认规则

1. 创建三个role: app-{name}-reader, app-{name}-writer, app-{name}-admin
2. 将app的创建者绑定到 app-{name}=admin
3. 将app的创建者所在的group绑定到 app-{name}-reader

简单一点说就是app的创建者有app的管理员权限, 创建者所在组的成员有app的读权限
