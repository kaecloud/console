FROM python:3.6.6-alpine
MAINTAINER yuyang <yyangplus@gmail.com>

RUN mkdir -p /kae/app
ADD . /kae/app

WORKDIR /kae/app
RUN apk add --no-cache git openssh libffi-dev openssl-dev alpine-sdk && \
    pip install --no-cache-dir pipenv && \
    pipenv install --system --deploy && \
    apk del alpine-sdk

EXPOSE 5000

ENTRYPOINT ["gunicorn", "console.app:app", "-c", "gunicorn_config.py"]
