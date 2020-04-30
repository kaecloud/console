from flask import request, redirect, url_for, current_app, abort, g
from flask_admin.contrib import sqla

from console.models import (
    App, Release, AppYaml, SpecVersion, OPLog, User,
    Role, UserRoleBinding, GroupRoleBinding, check_rbac,
    RBACAction
)
from console.ext import db
from console.config import FAKE_USER
from console.models.user import get_current_user


class ConsoleModelView(sqla.ModelView):
    def is_accessible(self):
        if current_app.config['DEBUG']:
            g.user = User(FAKE_USER)
            return True
        else:
            g.user = get_current_user()
            if not g.user:
                abort(403, "please login")
            return check_rbac(None, [RBACAction.KAE_ADMIN])


def init_admin(admin):
    admin.add_view(ConsoleModelView(App, db.session, endpoint='app_db_admin'))
    admin.add_view(ConsoleModelView(AppYaml, db.session, endpoint='app_yaml_db_admin'))
    admin.add_view(ConsoleModelView(Release, db.session, endpoint='release_db_admin'))
    admin.add_view(ConsoleModelView(SpecVersion, db.session, endpoint='spec_version_db_admin'))
    admin.add_view(ConsoleModelView(OPLog, db.session, endpoint='oplog_db_admin'))

