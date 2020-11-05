# 修改集群

    decode64 `kubectl get secrets kae-console-config -n kae -o=jsonpath='{.data.config\.py}'`
    
这是当前集群的配置, 改一下 `CLUSTER_CFG`, 然后重建kae namespace的相关的三个pod就好