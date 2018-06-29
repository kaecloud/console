import os
import re
import json
import shutil
from contextlib import contextmanager
from subprocess import Popen, PIPE, STDOUT, check_call, CalledProcessError

from flask import abort
import docker
import docker.errors

from console.config import REPO_DATA_DIR
from console.libs.utils import construct_full_image_name, logger
from console.libs.k8s import ApiException


@contextmanager
def handle_k8s_err(msg_prefix, clean_func=None):
    try:
        yield
    except ApiException as e:
        if clean_func:
            clean_func()
        logger.exception(msg_prefix)
        abort(e.status, msg_prefix)
    except Exception as e:
        if clean_func:
            clean_func()
        logger.exception(msg_prefix)
        abort(500, msg_prefix)


class CodeFetcher(object):
    @classmethod
    def _run_cmd(cls, args):
        # clone code
        p = Popen(args, stdout=PIPE, stderr=STDOUT, env=os.environ.copy())

        for line in iter(p.stdout.readline, ""):
            if not line:
                break
            if isinstance(line, bytes):
                line = line.decode('utf8')
            line = line.strip(" \n")
            yield line
        p.wait()
        if p.returncode:
            raise BuildError(make_msg("Cloning", success=False, error="git clone error: {}".format(p.returncode)))

    @classmethod
    def clone(cls, git, dest_dir, branch, commit):
        shutil.rmtree(dest_dir, ignore_errors=True)
        args = ['git',  'clone',  '--recursive', '--progress', git, dest_dir]
        yield from cls._run_cmd(args)

    @classmethod
    def pull(cls, dest_dir, branch, commit):
        pass

    @classmethod
    def clone_or_pull(cls, git, dest_dir, branch, commit):
        if os.path.isdir(dest_dir):
            yield from cls.pull(dest_dir, branch, commit)
        else:
            yield from cls.clone(git, dest_dir, branch, commit)


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
