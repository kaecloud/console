# -*- coding:utf-8 -*-
from datetime import datetime, timedelta
import copy

import pytest
from console.models import App, Role, UserRoleBinding, GroupRoleBinding, RBACAction, prepare_roles_for_new_app, delete_roles_relate_to_app
from console.config import FAKE_USER
from .prepare import (
    default_appname, default_git, default_tag, default_specs_text,
)


def prepare(appname, username, group_id):
    App.get_or_create(appname, git=default_git, apptype="web")
    app = App.get_by_name(appname)
    assert app.name == appname

    app_reader_name, app_writer_name, app_admin_name = f"app-{app.name}-reader", f"app-{app.name}-writer", f"app-{app.name}-admin"

    app_reader = Role.create(app_reader_name, [app], [RBACAction.GET])
    app_writer = Role.create(app_writer_name, [app], [RBACAction.GET])
    app_admin = Role.create(app_admin_name, [app], [RBACAction.GET])

    UserRoleBinding.create(username, app_admin)
    GroupRoleBinding.create(group_id, app_reader)


def test_role(test_db):
    username = "test_user"
    group_id = "abcd-1234"

    App.get_or_create(default_appname, git=default_git, apptype="web")
    app = App.get_by_name(default_appname)
    assert app.name == default_appname

    rolename = f"app-{default_appname}-reader"
    role = Role.create(rolename, [app], [RBACAction.GET])
    assert Role.get_by_name("134haha") is None
    assert Role.get(role.id+100000) is None

    role = Role.get(role.id)
    assert role.name == rolename

    UserRoleBinding.create(username, role)
    GroupRoleBinding.create(group_id, role)

    roles = UserRoleBinding.get_roles_by_name(username)
    assert len(roles) == 1
    assert roles[0].name == rolename

    roles = GroupRoleBinding.get_roles_by_id(group_id)
    assert len(roles) == 1
    assert roles[0].name == rolename

    role.delete()
    roles = UserRoleBinding.get_roles_by_name(username)
    assert len(roles) == 0

    roles = GroupRoleBinding.get_roles_by_id(group_id)
    assert len(roles) == 0


def test_delete_binding(test_db):
    username = "test_user"
    group_id = "abcd-1234"
    reader_name = f"app-{default_appname}-reader"
    admin_name = f"app-{default_appname}-admin"
    prepare(default_appname, username, group_id)

    roles = UserRoleBinding.get_roles_by_name(username)
    assert len(roles) == 1
    assert roles[0].name == admin_name
    users = roles[0].users

    assert len(users.all()) == 1
    users.all()[0].delete()

    role = Role.get_by_name(admin_name)
    assert len(role.users.all()) == 0

    roles = GroupRoleBinding.get_roles_by_id(group_id)
    assert len(roles) == 1
    assert roles[0].name == reader_name
    groups = roles[0].groups
    assert len(groups.all()) == 1
    groups.all()[0].delete()

    role = Role.get_by_name(reader_name)
    assert len(role.groups.all()) == 0


def test_delete_app(test_db):
    username = "test_user"
    group_id = "abcd-1234"
    reader_name = f"app-{default_appname}-reader"
    writer_name = f"app-{default_appname}-reader"
    admin_name = f"app-{default_appname}-admin"
    prepare(default_appname, username, group_id)

    app = App.get_by_name(default_appname)
    assert app is not None

    assert app.roles.count() == 3

    assert Role.get_by_name(reader_name) is not None
    assert Role.get_by_name(writer_name) is not None
    assert Role.get_by_name(admin_name) is not None
    delete_roles_relate_to_app(app)
    assert Role.get_by_name(reader_name) is None
    assert Role.get_by_name(writer_name) is None
    assert Role.get_by_name(admin_name) is None

    assert app.roles.count() == 0
    app.delete()

