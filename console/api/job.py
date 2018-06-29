import os
import re
import yaml
import json
import logging
from flask import abort, g, stream_with_context, current_app
import time
import shlex
import copy

from addict import Dict
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError
from webargs.flaskparser import use_args

from console.libs.validation import JobArgsSchema
from console.libs.view import create_api_blueprint, DEFAULT_RETURN_VALUE, user_require
from console.models import Job
from console.models.specs import load_job_specs
from console.libs import sse
from console.libs.k8s import kube_api, ApiException
from console.libs.cloner import Cloner
from console.config import JOBS_ROOT_DIR
from console.libs.utils import bearychat_sendmsg, logger
from .util import handle_k8s_err

bp = create_api_blueprint('job', __name__, 'job')


@bp.route('/')
@user_require(False)
def list_job():
    """
    List all the jobs associated with the current logged in user, for
    administrators, list all apps
    ---
    responses:
      200:
        description: A list of job owned by current user
        schema:
          type: array
          items:
            $ref: '#/definitions/Job'
        examples:
          application/json:
          - id: 10001
            created: "2018-03-21 14:54:06"
            updated: "2018-03-21 14:54:07"
            name: "test-app"
            git: "git@github.com:projecteru2/console.git"
            specs_text: hahaha
    """
    return g.user.list_job()


@bp.route('/', methods=['POST'])
@use_args(JobArgsSchema())
@user_require(False)
def create_job(args):
    """
    create a new job
    ---
    responses:
      200:
        description: Error message
        schema:
          type: object
          $ref: '#/definitions/Error'
        examples:
          application/json:
            error: null
    """
    specs_text = args.get('specs_text', None)
    if specs_text:
        try:
            yaml_dict = yaml.load(specs_text)
        except yaml.YAMLError as e:
            return abort(403, 'specs text is invalid yaml {}'.format(str(e)))
    else:
        # construct specs dict from args
        command = shlex.split(args['command'])
        if args['shell'] or args.get('gpu', 0) > 0:
            if len(command) > 2 and (command[0] != 'sh' or command[1] != '-c'):
                command = ['sh', '-c'] + command

        yaml_dict = {
            'containers': [
                {
                    'name': args['jobname'],
                    'image': args['image'],
                    'command': command,
                }
            ]
        }
        copy_list = ('jobname', 'git', 'branch', 'commit', 'autoRestart', 'comment')
        for field in copy_list:
            if field in args:
                yaml_dict[field] = args[field]
        if 'gpu' in args:
            yaml_dict['containers'][0]['gpu'] = args['gpu']
    try:
        specs = load_job_specs(yaml_dict)
    except ValidationError as e:
        return abort(400, 'specs text is invalid {}'.format(str(e)))
    try:
        job = Job.create(
            name=specs.jobname, git=specs.get('git'), branch=specs.get('branch'),
            commit=specs.get('commit'), comment=specs.get('comment'), status="Pending",
            specs_text=yaml.dump(specs.to_dict())
        )
    except IntegrityError as e:
        return abort(400, 'job is duplicate')
    except ValueError as e:
        return abort(400, str(e))

    def clean_func():
        """
        clean database when got an error.
        :return:
        """
        job.delete()

    job_dir = os.path.join(JOBS_ROOT_DIR, specs.jobname)
    code_dir = os.path.join(job_dir, "code")
    if specs.git:
        try:
            cloner = Cloner(repo=specs.git, dst_directory=code_dir,
                            branch=specs.branch, commit_id=specs.commit)
            cloner.clone_and_copy()
        except Exception as e:
            job.delete()

            logger.exception("clone error")
            abort(500, "clone and copy code error: {}".format(str(e)))

    with handle_k8s_err("Error when create job", clean_func=clean_func):
        kube_api.create_job(specs)

    try:
        job.grant_user(g.user)
    except IntegrityError as e:
        pass

    return DEFAULT_RETURN_VALUE


@bp.route('/<jobname>', methods=['DELETE'])
@user_require(False)
def delete_job(jobname):
    """
    Delete a single job
    ---
    parameters:
      - name: jobname
        in: path
        type: string
        required: true
    responses:
      200:
        description: error message
        schema:
          $ref: '#/definitions/Error'
        examples:
          application/json:
            error: null
    """
    job = Job.get_by_name(jobname)
    if not job:
        abort(404, "job {} not found".format(jobname))

    with handle_k8s_err("Error when delete job"):
        kube_api.delete_job(jobname, ignore_404=True)
    job.delete()
    return DEFAULT_RETURN_VALUE


@bp.route('/<jobname>/restart', methods=['PUT'])
@user_require(False)
def restart_job(jobname):
    """
    Restart a single job
    ---
    parameters:
      - name: jobname
        in: path
        type: string
        required: true
    responses:
      200:
        description: error message
        schema:
          $ref: '#/definitions/Error'
        examples:
          application/json:
            error: null
    """
    job = Job.get_by_name(jobname)
    if not job:
        abort(404, "job {} not found".format(jobname))

    job.inc_version()

    with handle_k8s_err("Error when delete job"):
        kube_api.delete_job(jobname, ignore_404=True)
    specs = job.specs
    # FIXME: need to wait the old job to be deleted
    while True:
        try:
            kube_api.get_job(jobname)
        except ApiException as e:
            if e.status == 404:
                break
            else:
                logger.exception("kubernetes error")
                abort(500, "kubernetes error")
        except:
            logger.exception("kubernetes error")
            abort(500, "kubernetes error")
        time.sleep(2)
    with handle_k8s_err("Error when create job"):
        kube_api.create_job(specs)
    return DEFAULT_RETURN_VALUE


@bp.route('/<jobname>/log')
@user_require(False)
def get_job_log(jobname):
    """
    get log belong to job
    ---
    responses:
      200:
        description: get logs
        schema:
          type: object
        examples:
          application/json:
            data: "haha"
    """
    job = Job.get_by_name(name=jobname)
    if not job:
        abort(404, "job {} not found".format(jobname))

    try:
        pods = kube_api.get_job_pods(jobname)
        if pods.items:
            podname = pods.items[0].metadata.name
            data = kube_api.get_pod_log(podname=podname)
            return {'data': data}
        else:
            return {'data': "no log, please retry"}
    except ApiException as e:
        abort(e.status, "Error when get job log: {}".format(str(e)))
    except Exception as e:
        abort(500, "Error when get job log: {}".format(str(e)))


@bp.route('/<jobname>/log/events')
@user_require(False)
def get_job_log_events(jobname):
    """
    SSE endpoint fo job log
    ---
    responses:
      200:
        description: event stream
        schema:
          type: object
    """
    @stream_with_context
    def generator():
        job = Job.get_by_name(jobname)
        if not job:
            yield sse.make_errmsg("job {} not found".format(jobname))
            return
        try:
            pods = kube_api.get_job_pods(jobname)
            if pods.items:
                podname = pods.items[0].metadata.name
                for line in kube_api.follow_pod_log(podname=podname):
                    yield sse.make_msg(line)
                yield sse.make_close_msg('')
            else:
                yield sse.make_close_msg("no log, please retry")
        except ApiException as e:
            yield sse.make_errmsg("Error when create job: {}".format(str(e)))
        except Exception as e:
            yield sse.make_errmsg("Error when create job: {}".format(str(e)))

    return current_app.response_class(
        generator(),
        mimetype='text/event-stream',
    )
