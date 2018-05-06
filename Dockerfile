FROM python:3.5.5-alpine
MAINTAINER yuyang <yyangplus@gmail.com>

RUN mkdir -p /kae/app
ADD . /kae/app

WORKDIR /kae/app
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories && \
    apk update && \
    apk add --no-cache libffi-dev openssl-dev linux-headers alpine-sdk libstdc++ && \
	pip install -i https://mirrors.aliyun.com/pypi/simple/ -U pipenv && \
	pipenv install --system --deploy && \
	apk del alpine-sdk && \
	apk add --no-cache git openssh

EXPOSE 5000

ENTRYPOINT ["gunicorn", "console.app:app", "-c", "gunicorn_config.py"]
