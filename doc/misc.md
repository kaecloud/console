# 修改集群

    decode64 `kubectl get secrets kae-console-config -n kae -o=jsonpath='{.data.config\.py}'`
    
这是当前集群的配置, 改一下 `CLUSTER_CFG`, 假设新的配置文件是new_config.py, 用以下命令创建新的secret

    kubectl create secret generic kae-console-config --from-file=config.py=new_config.py -n kae
    
然后重建kae namespace的相关的三个pod就好
