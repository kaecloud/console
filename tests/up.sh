#!/bin/bash

# Copyright 2017 The Kubernetes Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "$DOCKER_PASS" | docker login -u "$DOCKER_USER" --password-stdin

if test -e kubectl; then
  echo "skipping download of kubectl"
else
  echo "downloading kubectl..."
  curl -Lo kubectl https://storage.googleapis.com/kubernetes-release/release/v1.12.0/bin/linux/amd64/kubectl && \
      chmod +x kubectl && sudo mv kubectl /usr/local/bin/
fi

mkdir -p ${HOME}/.kube
touch ${HOME}/.kube/config
export KUBECONFIG=${HOME}/.kube/config

echo "starting Kubernetes cluster..."
K8S_VERSION=v1.11
KDC_SHA=e505612125948bab5a415ec3e5c1f9f26324488f28286e005fd1f3a0a6292c49
curl -Lo $DIR/dind-cluster-$K8S_VERSION.sh https://github.com/kubernetes-sigs/kubeadm-dind-cluster/releases/download/v0.1.0/dind-cluster-$K8S_VERSION.sh && \
  chmod +x $DIR/dind-cluster-$K8S_VERSION.sh

echo "$KDC_SHA  $DIR/dind-cluster-$K8S_VERSION.sh" | sha256sum -c - || exit 10
$DIR/dind-cluster-$K8S_VERSION.sh up

kubectl config use-context dind

echo "Kubernetes cluster:"
kubectl get nodes -o wide

export TAG=dev
export ARCH=amd64
export REGISTRY=kaecloud/console
DEV_IMAGE=${REGISTRY}:${TAG}

echo "building container..."

docker build -t ${DEV_IMAGE} ${DIR}/../

echo "copying docker image to cluster..."
${DIR}/dind-cluster-v1.11.sh copy-image ${DEV_IMAGE}

docker run --name kae-console-test -it --rm --network host \
       -v `pwd`/config/dev:/etc/kae-console \
       -v /var/run/docker.sock:/var/run/docker.sock \
       -v $HOME/.docker/config.json:/root/.docker/config.json \
       -v $HOME/.kube/config:/root/.kube/config \
       -v `pwd`:/kae/app  \
       ${DEV_IMAGE}

docker exec -d kae-console-test sh bin/run-pods-watcher
docker exec -d kae-console-test  C_FORCE_ROOT=1 celery -A console.app:celery worker --autoscale=4,1 -B
