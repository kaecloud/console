import json
from flask import request, redirect, url_for, current_app, abort, g
from flask_admin.contrib import sqla
from wtforms import ValidationError

from console.models import (
    App, Release, AppYaml, DeployVersion, OPLog, User, Group,
    Role, UserRoleBinding, GroupRoleBinding,
    check_rbac, RBACAction, str2action,
)
from console.ext import db
from console.config import FAKE_USER
from console.models.user import get_current_user
from console.libs.utils import cluster_exists


class ConsoleModelView(sqla.ModelView):
    form_excluded_columns = ["created", "updated"]

    def is_accessible(self):
        if current_app.config['DEBUG']:
            g.user = User(FAKE_USER)
            return True
        else:
            g.user = get_current_user()
            if not g.user:
                return False
            return check_rbac([RBACAction.KAE_ADMIN])


def _validate_actions(form, field):
    txt = field.data
    try:
        lst = json.loads(txt)
    except:
        msg = "invalid json list"
        raise ValidationError(msg)
    for act_txt in lst:
        try:
            str2action(act_txt)
        except AttributeError:
            msg = f"invalid action {act_txt}"
            raise ValidationError(msg)


def _validate_clusters(form, field):
    txt = field.data
    try:
        lst = json.loads(txt)
    except:
        msg = "invalid json list"
        raise ValidationError(msg)
    for cluster in lst:
        if not cluster_exists(cluster):
            msg = f"cluster {cluster} doesn't exist"
            raise ValidationError(msg)


class AppModelView(ConsoleModelView):
    column_searchable_list = ['name']


class ReleaseModelView(ConsoleModelView):
    column_searchable_list = ['image', 'specs_text']


class RoleModelView(ConsoleModelView):
    column_searchable_list = ['name']
    form_args = {
        "actions": {
            "label": "Actions",
            "validators": [_validate_actions],
        },
        "clusters": {
            "label": "Clusters",
            "validators": [_validate_clusters],
        }
    }


def _get_user_choices():
    users = User.get_all()
    return [(user.username, user.nickname) for user in users]


class UserRoleBindingModelView(ConsoleModelView):
    form_choices = {
        "username": _get_user_choices(),
    }
    column_searchable_list = ['username']


def _get_group_choices():
    groups = Group.get_all()
    return [(group.id, group.name) for group in groups]


class GroupRoleBindingModelView(ConsoleModelView):
    form_choices = {
        "group_id": _get_group_choices(),
    }


def init_admin(admin):
    admin.add_view(AppModelView(App, db.session, endpoint='app_db_admin'))
    admin.add_view(ConsoleModelView(AppYaml, db.session, endpoint='app_yaml_db_admin'))
    admin.add_view(ReleaseModelView(Release, db.session, endpoint='release_db_admin'))
    admin.add_view(ConsoleModelView(DeployVersion, db.session, endpoint='deploy_version_db_admin'))
    admin.add_view(RoleModelView(Role, db.session, endpoint='role_db_admin'))
    admin.add_view(UserRoleBindingModelView(UserRoleBinding, db.session, endpoint="user_role_binding_db_admin"))
    admin.add_view(GroupRoleBindingModelView(GroupRoleBinding, db.session, endpoint="group_role_binding_db_admin"))
    admin.add_view(ConsoleModelView(OPLog, db.session, endpoint='oplog_db_admin'))

