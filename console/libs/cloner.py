# encoding: utf-8

import os
import shutil
import hashlib
import re
import logging
import urllib.parse
from subprocess import Popen, PIPE, STDOUT, CalledProcessError
import subprocess
from console.config import JOBS_REPO_DATA_DIR


class GitError(Exception):
    def __init__(self, errstr):
        self.errstr = errstr

    def __str__(self):
        return self.errstr


class GitCredentialProvider:
    __https_pattern = re.compile(r'https:\/\/(\w+@\w+)?[\w.\/\-+]*.git')
    __ssh_pattern = re.compile(r'(ssh:\/\/)?\w+@[\w.]+:(\d+\/)?[\w-]+\/[\w\-+]+\.git')

    @classmethod
    def get_repo_type(cls, repo):
        if cls.__ssh_pattern.match(repo):
            return 'ssh'
        elif cls.__https_pattern.match(repo):
            return'https'
        return None

    def __init__(self, ssh_key=None, https_username=None, https_password=None):
        pass

    @property
    def ssh_key(self):
        raise NotImplementedError

    @property
    def https_username(self):
        raise NotImplementedError

    @property
    def https_password(self):
        raise NotImplementedError


class Cloner:
    """Do the git clone stuff in another thread"""

    __ref_pattern = re.compile(r'(?P<hash>\w+)\s(?P<ref>[\w/\-]+)')

    def __init__(self, repo, dst_directory, branch=None, commit_id=None,
                 crediential=None):
        """Init
            crediential: a credientialProvider instance
        """
        self.repo = repo.strip()
        self.dst_directory = dst_directory
        self.branch = branch or 'master'
        self.commit_id = commit_id
        self.crediential = crediential

        self.ssh_key_path = None
        self.repo_path = None
        self.repo_url = None
        self.repo_type = GitCredentialProvider.get_repo_type(self.repo)

        if not self.repo_type:
            raise GitError('wrong repo type')

        # if self.repo_type == 'ssh' and self.crediential.ssh_key is None:
        #     raise GitError('ssh credential for {repo} must be provided.'.format(repo=repo))

        self.repo_hash = hashlib.sha1(self.repo.encode('utf-8')).hexdigest()

    def prepare_ssh_key(self, repo):
        ssh_key_dir = os.path.join('/tmp/kae/ssh_keys', self.repo_hash)
        if not os.path.exists(ssh_key_dir):
            os.makedirs(ssh_key_dir)
        self.ssh_key_path = os.path.join(ssh_key_dir, 'id')
        if not os.path.exists(self.ssh_key_path):
            with open(self.ssh_key_path, 'w') as f:
                f.write(self.crediential.ssh_key)
            os.chmod(self.ssh_key_path, 0o600)  # prevent WARNING: UNPROTECTED PRIVATE KEY FILE!

    @classmethod
    def exec_git_command(cls, args, cwd=None, env=None):
        p = Popen(['git'] + args, stdout=PIPE, stderr=STDOUT, cwd=cwd, env=env)

        lines = []
        for line in iter(p.stdout.readline, ""):
            if not line:
                break
            if isinstance(line, bytes):
                line = line.decode('utf8')
            line = line.strip(" \n")
            lines.append(line)
            yield line
        p.wait()
        if p.returncode:
            raise GitError("git clone error: {} {}".format(p.returncode, ''.join(lines)))

    @classmethod
    def add_credential_to_https_url(cls, url, username, password):
        parsed = urllib.parse.urlparse(url)
        if username is not None:
            host_and_port = parsed.hostname
            if parsed.port:
                host_and_port += ':' + parsed.port
            if password:
                parsed = parsed._replace(netloc='{}:{}@{}'.format(username, password, host_and_port))
            else:
                parsed = parsed._replace(netloc='{}@{}'.format(username, host_and_port))
        return parsed.geturl()

    def get_heads(self):
        heads = {}
        for line in self.exec_git_command(['show-ref'], cwd=self.repo_path):
            logging.debug(line)
            group = self.__ref_pattern.search(line)
            if group:
                heads[group.group('ref')] = group.group('hash')
        return heads

    def clone(self):
        if self.repo_type == 'ssh':
            env = {**os.environ,
                   # 'GIT_SSH_COMMAND': 'ssh -oStrictHostKeyChecking=no -i {ssh_key_path}'.format(ssh_key_path=ssh_key_path)
                   }
            yield from self.exec_git_command(
                env=env,
                cwd=JOBS_REPO_DATA_DIR,
                args=['clone', self.repo, '--recursive', self.repo_hash],
            )
        else:
            yield from self.exec_git_command(
                cwd=JOBS_REPO_DATA_DIR,
                args=['clone', self.repo_url, '--recursive', self.repo_hash],
            )

    def fetch(self):
        if self.repo_type == 'ssh':
            env = {**os.environ,
                   # 'GIT_SSH_COMMAND': 'ssh -oStrictHostKeyChecking=no -i {ssh_key_path}'.format(ssh_key_path=ssh_key_path)
                   }
            yield from self.exec_git_command(
                env=env,
                cwd=self.repo_path,
                args=['fetch'],
            )
        else:
            yield from self.exec_git_command(
                cwd=self.repo_path,
                args=['fetch', self.repo_url, '+refs/heads/*:refs/remotes/origin/*'],
            )

    def clone_and_copy(self):
        if not os.path.exists(JOBS_REPO_DATA_DIR):
            os.makedirs(JOBS_REPO_DATA_DIR)
        self.repo_path = os.path.join(JOBS_REPO_DATA_DIR, self.repo_hash)

        #TODO: maybe need to figure out better to supply credential
        # if self.repo_type == 'ssh':
        #     self.prepare_ssh_key(self.repo)
        # elif self.repo_type == 'https':
        #     self.repo_url = self.repo
        #     if self.crediential.https_username:
        #         self.repo_url = self.add_credential_to_https_url(
        #             self.repo, username=self.crediential.https_username, password=self.crediential.https_password)

        if not os.path.exists(self.repo_path):  # Then clone it
            gen = self.clone()
        else:
            gen = self.fetch()

        # ignore all output
        for _ in gen:
            pass

        # get commit_id
        if not self.commit_id and self.branch:
            heads = self.get_heads()
            self.commit_id = heads.get('refs/remotes/origin/{branch}'.format(branch=self.branch), None)
            if not self.commit_id:
                raise GitError('Branch {branch} not found for {repo}.'.format(branch=self.branch, repo=self.repo))

        # Arcive commit_id
        try:
            # remove dst dir and create new empty dst dir
            shutil.rmtree(self.dst_directory, ignore_errors=True)
            os.makedirs(self.dst_directory)

            subprocess.check_call("git archive {} --format tar.gz | tar -xz -C {}".format(self.commit_id, self.dst_directory), shell=True, cwd=self.repo_path)
        except CalledProcessError as e:
            raise GitError("error to activate commit id {}".format(str(e)))
