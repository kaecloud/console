# -*- coding:utf-8 -*-
from datetime import datetime, timedelta
import copy

import pytest
from console.models import App, Release, OPLog, OPType, User
from console.config import FAKE_USER
from .prepare import (
    default_appname, default_git, default_tag, default_specs_text,
)


def create_normal_user():
    data = {
        'username': 'sheldon',
        'nickname': 'Sheldon Lee Cooper',
        'email': 'sheldon@sheldon.com',
        'privileged': 0,
    }
    return User.create(**data)


def create_privileged_user():
    data = {
        'username': 'root',
        'nickname': 'Sheldon Lee Cooper',
        'email': 'sheldon@sheldon.com',
        'privileged': 1,
    }
    return User.create(**data)


def test_app(test_db):
    App.get_or_create(default_appname, git=default_git, apptype="web")
    app = App.get_by_name(default_appname)
    assert app.name == default_appname

    app.delete()
    app = App.get_by_name(default_appname)
    assert app is None


def test_release(test_db):
    app = App.get_or_create(default_appname, git=default_git, apptype="web")
    Release.create(app, default_tag, default_specs_text)


def test_oplog(test_db):
    create_at = datetime.now() - timedelta(seconds=1)
    OPLog.create(user_id=FAKE_USER['id'],
                 app_id=1,
                 appname=default_appname,
                 action=OPType.SCALE_APP,
                 content="{'foo': 'bar'}")

    OPLog.create(user_id=FAKE_USER['id'],
                 app_id=1,
                 appname=default_appname,
                 action=OPType.DELETE_APP,
                 content="{'foo': 'bar'}")

    query_by_time_window = OPLog.get_by(time_window=(create_at, datetime.now()))
    assert len(query_by_time_window) == 2
    query_by_false_time_window = OPLog.get_by(time_window=(None, create_at))
    assert not query_by_false_time_window

    query_all = OPLog.get_by()
    assert len(query_all) == 2
    query_with_limit = OPLog.get_by(limit=1)
    assert len(query_with_limit) == 1

    query_by_appname = OPLog.get_by(appname=default_appname)
    assert len(query_by_appname) == 2
