In order to simplify the development, we recommend running code in docker container

    docker run --name kae-console -it --rm --network host -v `pwd`/config/dev:/etc/k8s-secret-volume -v /var/run/docker.sock:/var/run/docker.sock -v /home/yangyu/.kube/config:/root/.kube/config -v `pwd`:/kae/app  -e USE_KUBECONFIG=true -p 5000:5000 --entrypoint sh yuyang0/kae-console:0.0.1-alpha1

then you can start gunicorn server in container

    gunicorn console.app:app -c gunicorn_config.py --reload

you also need start celery workers

    docker exec -it kae-console sh
    celery -A console.app:celery worker --autoscale=4,2 -B -Ofair

A redis instance is also needed