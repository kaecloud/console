import yaml
import json
from flask import abort, g, Response, stream_with_context
import time

from addict import Dict
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError
from webargs.flaskparser import use_args

from console.libs.validation import RegisterSchema, UserSchema, RollbackSchema, SecretSchema, ScaleSchema
from console.libs.view import create_api_blueprint, DEFAULT_RETURN_VALUE, user_require
from console.models import App, Release, User, OPLog, OPType
from console.models.specs import load_specs
from console.libs.k8s import kube_api
from console.libs.k8s import ApiException
from console.config import DEFAULT_REGISTRY


bp = create_api_blueprint('job', __name__, 'job')


@bp.route('/job/run')
@user_require(False)
def run_job():
    pass
