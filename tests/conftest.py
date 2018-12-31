# -*- coding: utf-8 -*-

import pytest
import subprocess
import threading
from urllib.parse import urlparse

from .prepare import (
    default_appname, default_git
)
from console.app import create_app
from console.config import FAKE_USER
from console.ext import db, rds
from console.libs.utils import logger


json_headers = {'Content-Type': 'application/json'}


@pytest.fixture
def app(request):
    app = create_app()
    app.config['DEBUG'] = True

    ctx = app.app_context()
    ctx.push()

    def tear_down():
        ctx.pop()

    request.addfinalizer(tear_down)
    return app


@pytest.fixture
def client(app):
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_db(request, app):

    def check_service_host(uri):
        """只能在本地或者容器里跑测试"""
        u = urlparse(uri)
        return u.hostname in ('localhost', '127.0.0.1')

    if not (check_service_host(app.config['SQLALCHEMY_DATABASE_URI']) and check_service_host(app.config['REDIS_URL'])):
        raise Exception('Need to run test on localhost or in container')

    db.create_all()

    def teardown():
        db.session.remove()
        db.drop_all()
        rds.flushdb()

    request.addfinalizer(teardown)


# @pytest.fixture(scope='session')
# def test_app_image():
#     if not core_online:
#         pytest.skip(msg='one or more eru-core is offline, skip core-related tests')

#     specs = make_specs()
#     appname = default_appname
#     builds_map = {stage_name: pb.Build(**build) for stage_name, build in specs.builds.items()}
#     core_builds = pb.Builds(stages=specs.stages, builds=builds_map)
#     opts = pb.BuildImageOptions(name=appname,
#                                 user=appname,
#                                 uid=12345,
#                                 tag=default_sha,
#                                 builds=core_builds)
#     core = get_core(BUILD_ZONE)
#     build_image_messages = list(core.build_image(opts))
#     image_tag = ''
#     for m in build_image_messages:
#         assert not m.error

#     image_tag = m.progress
#     assert '{}:{}'.format(default_appname, default_sha) in image_tag
#     return image_tag
