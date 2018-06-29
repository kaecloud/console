# -*- coding: utf-8 -*-

import json
import yaml
from sqlalchemy import event, DDL
from sqlalchemy.exc import IntegrityError
from flask import g
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.utils import cached_property

from console.ext import db
from console.models.base import BaseModelMixin
from console.models.specs import load_job_specs
from console.libs.utils import logger


class Job(BaseModelMixin):
    name = db.Column(db.CHAR(64), nullable=False, unique=True)
    git = db.Column(db.String(255), nullable=False, default='')
    branch = db.Column(db.String(255), nullable=False, default='')
    commit = db.Column(db.String(255), nullable=False, default='')

    specs_text = db.Column(db.Text)
    nickname = db.Column(db.String(64), nullable=False)
    comment = db.Column(db.Text)

    version = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(64), nullable=False, default='')

    def __str__(self):
        return '<{}:{}>'.format(self.name, self.git)

    @classmethod
    def get_or_create(cls, name, git=None, branch=None, commit=None, specs_text=None, comment=None, status=None):
        job = cls.get_by_name(name)
        if job:
            return job
        return cls.create(
                name=name, git=git, branch=branch, commit=commit, specs_text=specs_text,
                comment=comment, status=status)

    @classmethod
    def create(cls, name, git=None, branch=None, commit=None, specs_text=None, comment=None, status=None):
        try:
            job = cls(
                name=name, git=git, branch=branch, commit=commit, specs_text=specs_text,
                nickname=g.user.nickname, comment=comment, status=status)
            db.session.add(job)
            db.session.commit()
        except IntegrityError as e:
            logger.warn('Fail to create Job %s %s, duplicate', name)
            db.session.rollback()
            raise e
        return job

    def update_status(self, status):
        try:
            self.status = status
            db.session.add(self)
            db.session.commit()
        except StaleDataError:
            db.session.rollback()
        except Exception:
            db.session.rollback()

    def inc_version(self):
        self.version += 1
        try:
            db.session.add(self)
            db.session.commit()
        except StaleDataError:
            db.session.rollback()
        except Exception:
            db.session.rollback()

    @classmethod
    def get_by_name(cls, name):
        return cls.query.filter_by(name=name).first()

    @classmethod
    def get_by_user(cls, user_id):
        """拿这个user可以有的job, 跟job自己的nickname没关系."""
        names = JobUserRelation.get_jobname_by_user_id(user_id)
        return [cls.get_by_name(n) for n in names]

    def grant_user(self, user):
        JobUserRelation.create(self, user)

    def revoke_user(self, user):
        JobUserRelation.query.filter_by(jobname=self.name, user_id=user.id).delete()
        db.session.commit()

    def list_users(self):
        from console.models.user import User
        user_ids = [r.user_id for r in
                    JobUserRelation.query.filter_by(jobname=self.name).all()]
        users = [User.get(id_) for id_ in user_ids]
        return users

    @cached_property
    def specs(self):
        dic = yaml.load(self.specs_text)
        return load_job_specs(dic)

    def delete(self):
        """
        the caller must ensure the all kubernetes objects have been deleted
        :return:
        """
        jobname = self.name

        # delete all permissions
        JobUserRelation.query.filter_by(jobname=jobname).delete()
        return super(Job, self).delete()


class JobUserRelation(BaseModelMixin):
    __table_args__ = (
        db.UniqueConstraint('user_id', 'jobname'),
    )

    jobname = db.Column(db.CHAR(64), nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False)

    @classmethod
    def create(cls, app, user):
        relation = cls(jobname=app.name, user_id=user.id)
        try:
            db.session.add(relation)
            db.session.commit()
            return relation
        except IntegrityError:
            db.session.rollback()
            raise


event.listen(
    Job.__table__,
    'after_create',
    DDL('ALTER TABLE %(table)s AUTO_INCREMENT = 10001;'),
)
