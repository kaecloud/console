# -*- coding: utf-8 -*-

from flask import url_for, current_app

from .prepare import default_appname
from console.config import FAKE_USER
from console.models.app import App
from console.models.user import User


def test_login(test_db, client):
    current_app.config['DEBUG'] = False
    url = url_for('app.list_app')
    res = client.get(url)
    assert res.status_code == 302


def test_permissions(test_db, client):
    FAKE_USER['privileged'] = 0
    user = User.create(**FAKE_USER)

    res = client.get(url_for('app.list_app'))
    assert res.json == []
    res = client.get(url_for('app.get_app', appname=default_appname))
    assert res.status_code == 403

    app = App.get_by_name(default_appname)
    app.grant_user(user)

    res = client.get(url_for('app.list_app'))
    assert len(res.json) == 1
    res = client.get(url_for('app.get_app', appname=default_appname))
    assert res.status_code == 200
