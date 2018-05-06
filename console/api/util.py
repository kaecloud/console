import os
import re
import json
import shutil
from subprocess import Popen, PIPE, STDOUT, check_call, CalledProcessError

import docker
import docker.errors

from console.config import REPO_DATA_DIR
from console.libs.utils import construct_full_image_name, logger


def make_errmsg(msg):
    return json.dumps({'success': False, 'error': msg}) + '\n'


def make_msg(phase, raw_data=None, success=True, error=None, msg=None, progress=None):
    d = {
        "success": success,
        "phase": phase,
        "raw_data": raw_data,
        'progress': progress,
        'msg': msg,
        'error': error,
    }
    return json.dumps(d) + '\n'


class BuildError(Exception):
    def __init__(self, errstr):
        self.errstr = errstr

    def __str__(self):
        return self.errstr


def build_image(appname, release):
    git_tag = release.tag
    specs = release.specs

    if not specs.builds:
        yield make_msg("Finished", msg="ignore empty builds")
        return
    if release.build_status:
        yield make_msg("Finished", msg="already builded")
        return

    # clone code
    repo_dir = os.path.join(REPO_DATA_DIR, appname)
    shutil.rmtree(repo_dir, ignore_errors=True)
    p = Popen(['git',  'clone',  '--recursive', '--progress', release.git, repo_dir], stdout=PIPE,
              stderr=STDOUT, env=os.environ.copy())

    for line in iter(p.stdout.readline, ""):
        if not line:
            break
        if isinstance(line, bytes):
            line = line.decode('utf8')
        line = line.strip(" \n")
        yield make_msg("Cloning", msg=line)
    p.wait()
    if p.returncode:
        raise BuildError(make_msg("Cloning", success=False, error="git clone error: {}".format(p.returncode)))

    try:
        check_call("git checkout {}".format(git_tag), shell=True, cwd=repo_dir)
    except CalledProcessError as e:
        raise BuildError(make_msg("Checkout", success=False, error="checkout tag error: {}".format(str(e))))

    client = docker.APIClient(base_url="unix:///var/run/docker.sock")
    for build in specs.builds:
        image_name_no_tag = construct_full_image_name(build.name, appname)
        image_tag = build.tag if build.tag else release.tag
        dockerfile = build.dockerfile
        if dockerfile is None:
            dockerfile = os.path.join(repo_dir, "Dockerfile")
        full_image_name = "{}:{}".format(image_name_no_tag, image_tag)

        # use docker to build image
        try:
            for line in client.build(path=repo_dir, dockerfile=dockerfile, tag=full_image_name):
                output_dict = json.loads(line.decode('utf8'))
                if 'stream' in output_dict:
                    yield make_msg("Building", raw_data=output_dict, msg=output_dict['stream'].rstrip("\n"))
        except docker.errors.APIError as e:
            raise BuildError(make_msg("Building", success=False, error="Building error: {}".format(str(e))))
        try:
            for line in client.push(full_image_name, stream=True):
                output_dict = json.loads(line.decode('utf8'))
                msg = "{}:{}\n".format(output_dict.get('id'), output_dict.get('status'))
                yield make_msg("Pushing", raw_data=output_dict, msg=msg.rstrip("\n"))
        except docker.errors.APIError as e:
            raise BuildError(make_msg("Pushing", success=False, error="pushing error: {}".format(str(e))))
        logger.debug("=========", full_image_name)
    release.update_build_status(True)
    yield make_msg("Finished", msg="build app {}'s release {} successfully".format(appname, git_tag))


class RemoteProgress(object):
    """
    Handler providing an interface to parse progress information emitted by git-push
    and git-fetch and to dispatch callbacks allowing subclasses to react to the progress.
    """
    _num_op_codes = 9
    BEGIN, END, COUNTING, COMPRESSING, WRITING, RECEIVING, RESOLVING, FINDING_SOURCES, CHECKING_OUT = \
        [1 << x for x in range(_num_op_codes)]
    STAGE_MASK = BEGIN | END
    OP_MASK = ~STAGE_MASK

    DONE_TOKEN = 'done.'
    TOKEN_SEPARATOR = ', '

    __slots__ = ('_cur_line',
                 '_seen_ops',
                 'error_lines',  # Lines that started with 'error:' or 'fatal:'.
                 'other_lines')  # Lines not denoting progress (i.e.g. push-infos).
    re_op_absolute = re.compile(r"(remote: )?([\w\s]+):\s+()(\d+)()(.*)")
    re_op_relative = re.compile(r"(remote: )?([\w\s]+):\s+(\d+)% \((\d+)/(\d+)\)(.*)")

    def __init__(self):
        self._seen_ops = []
        self._cur_line = None
        self.error_lines = []
        self.other_lines = []

    def _parse_progress_line(self, line):
        """Parse progress information from the given line as retrieved by git-push
        or git-fetch.

        - Lines that do not contain progress info are stored in :attr:`other_lines`.
        - Lines that seem to contain an error (i.e. start with error: or fatal:) are stored
        in :attr:`error_lines`.

        :return: list(line, ...) list of lines that could not be processed"""
        # handle
        # Counting objects: 4, done.
        # Compressing objects:  50% (1/2)   \rCompressing objects: 100% (2/2)   \rCompressing objects: 100% (2/2), done.
        self._cur_line = line
        if len(self.error_lines) > 0 or self._cur_line.startswith(('error:', 'fatal:')):
            self.error_lines.append(self._cur_line)
            return []

        sub_lines = line.split('\r')
        failed_lines = []
        for sline in sub_lines:
            # find escape characters and cut them away - regex will not work with
            # them as they are non-ascii. As git might expect a tty, it will send them
            last_valid_index = None
            for i, c in enumerate(reversed(sline)):
                if ord(c) < 32:
                    # its a slice index
                    last_valid_index = -i - 1
                # END character was non-ascii
            # END for each character in sline
            if last_valid_index is not None:
                sline = sline[:last_valid_index]
            # END cut away invalid part
            sline = sline.rstrip()

            cur_count, max_count = None, None
            match = self.re_op_relative.match(sline)
            if match is None:
                match = self.re_op_absolute.match(sline)

            if not match:
                self.line_dropped(sline)
                failed_lines.append(sline)
                continue
            # END could not get match

            op_code = 0
            remote, op_name, percent, cur_count, max_count, message = match.groups()  # @UnusedVariable

            # get operation id
            if op_name == "Counting objects":
                op_code |= self.COUNTING
            elif op_name == "Compressing objects":
                op_code |= self.COMPRESSING
            elif op_name == "Writing objects":
                op_code |= self.WRITING
            elif op_name == 'Receiving objects':
                op_code |= self.RECEIVING
            elif op_name == 'Resolving deltas':
                op_code |= self.RESOLVING
            elif op_name == 'Finding sources':
                op_code |= self.FINDING_SOURCES
            elif op_name == 'Checking out files':
                op_code |= self.CHECKING_OUT
            else:
                # Note: On windows it can happen that partial lines are sent
                # Hence we get something like "CompreReceiving objects", which is
                # a blend of "Compressing objects" and "Receiving objects".
                # This can't really be prevented, so we drop the line verbosely
                # to make sure we get informed in case the process spits out new
                # commands at some point.
                self.line_dropped(sline)
                # Note: Don't add this line to the failed lines, as we have to silently
                # drop it
                self.other_lines.extend(failed_lines)
                return failed_lines
            # END handle op code

            # figure out stage
            if op_code not in self._seen_ops:
                self._seen_ops.append(op_code)
                op_code |= self.BEGIN
            # END begin opcode

            if message is None:
                message = ''
            # END message handling

            message = message.strip()
            if message.endswith(self.DONE_TOKEN):
                op_code |= self.END
                message = message[:-len(self.DONE_TOKEN)]
            # END end message handling
            message = message.strip(self.TOKEN_SEPARATOR)

            self.update(op_code,
                        cur_count and float(cur_count),
                        max_count and float(max_count),
                        message)
        # END for each sub line
        self.other_lines.extend(failed_lines)
        return failed_lines

    def new_message_handler(self):
        """
        :return:
            a progress handler suitable for handle_process_output(), passing lines on to this Progress
            handler in a suitable format"""
        def handler(line):
            return self._parse_progress_line(line.rstrip())
        # end
        return handler

    def line_dropped(self, line):
        """Called whenever a line could not be understood and was therefore dropped."""
        pass

    def update(self, op_code, cur_count, max_count=None, message=''):
        """Called whenever the progress changes

        :param op_code:
            Integer allowing to be compared against Operation IDs and stage IDs.

            Stage IDs are BEGIN and END. BEGIN will only be set once for each Operation
            ID as well as END. It may be that BEGIN and END are set at once in case only
            one progress message was emitted due to the speed of the operation.
            Between BEGIN and END, none of these flags will be set

            Operation IDs are all held within the OP_MASK. Only one Operation ID will
            be active per call.
        :param cur_count: Current absolute count of items

        :param max_count:
            The maximum count of items we expect. It may be None in case there is
            no maximum number of items or if it is (yet) unknown.

        :param message:
            In case of the 'WRITING' operation, it contains the amount of bytes
            transferred. It may possibly be used for other purposes as well.

        You may read the contents of the current line in self._cur_line"""
        pass
