from flask import jsonify, g
from webargs.flaskparser import use_args

from console.libs.view import create_api_blueprint, DEFAULT_RETURN_VALUE, user_require
from console.libs.validation import (
    CreateRoleArgsSchema, CreateRoleBindingArgsSchema,
)
from console.models import (
    App, User, Group,
    Role, UserRoleBinding, GroupRoleBinding, str2action, get_roles_by_user
)

bp = create_api_blueprint('rbac', __name__, 'rbac')


@bp.route('/')
@user_require(True)
def list_roles():
    """
    List all roles
    """
    roles = get_roles_by_user(g.user)
    return jsonify([r.to_dict() for r in roles])


@bp.route('/', methods=['POST'])
@use_args(CreateRoleArgsSchema())
@user_require(True)
def create_role(args):
    """
    create a role
    """
    apps = []
    for appname in args["apps"]:
        app = App.get_by_name(appname)
        apps.append(app)
    actions = [str2action(act_txt) for act_txt in args.get("actions", [])]

    r = Role.create(
        name=args['name'],
        apps=apps,
        actions=actions,
        clusters=args.get('clusters', []),
    )
    for username in args.get("users", []):
        UserRoleBinding.create(username, r)
    for groupname in args.get("groups", []):
        grp = Group.get_by_name(groupname)
        GroupRoleBinding.create(grp['id'], r)

    return DEFAULT_RETURN_VALUE


@bp.route('/', methods=['POST'])
@use_args(CreateRoleBindingArgsSchema())
@user_require(True)
def create_role_binding(args):
    """
    create a role
    """
    r = Role.get_by_name(args['role_name'])
    for username in args.get("users", []):
        UserRoleBinding.create(username, r)
    for groupname in args.get("groups", []):
        grp = Group.get_by_name(groupname)
        GroupRoleBinding.create(grp['id'], r)

    return DEFAULT_RETURN_VALUE
