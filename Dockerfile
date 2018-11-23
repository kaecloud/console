FROM python:3.6.6-alpine
MAINTAINER yuyang <yyangplus@gmail.com>

RUN mkdir -p /kae/app
ADD . /kae/app

WORKDIR /kae/app
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories && \
    apk update && \
    apk add --no-cache gcc musl-dev python3-dev libffi-dev openssl-dev linux-headers alpine-sdk libstdc++ && \
	pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt && \
	apk del alpine-sdk && \
	apk add --no-cache git openssh

EXPOSE 5000

ENTRYPOINT ["gunicorn", "console.app:app", "-c", "gunicorn_config.py"]
