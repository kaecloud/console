FROM golang:1.10.2 as kaniko
RUN go get -u github.com/GoogleContainerTools/kaniko/tree/master/cmd/executor


FROM python:3.6.4-alpine
MAINTAINER yuyang <yyangplus@gmail.com>

RUN mkdir -p /opt/console
ADD . /opt/console
COPY --from=kaniko /go/bin/executor /usr/bin/executor

WORKDIR /opt/console
RUN apk add --no-cache alpine-sdk libstdc++ && \
	pip install -U -r requirements.txt && \
	apk del alpine-sdk
