# -*- coding: utf-8 -*-

import yaml
from sqlalchemy import event, DDL
from sqlalchemy.exc import IntegrityError
from flask import g
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.utils import cached_property

from console.ext import db
from console.models.base import BaseModelMixin
from console.libs.specs import load_job_specs
from console.libs.utils import logger


job_user_association = db.Table(
    'job_user_association',
    db.Column('job_id', db.Integer, db.ForeignKey('job.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)


class Job(BaseModelMixin):
    __tablename__ = "job"

    name = db.Column(db.CHAR(64), nullable=False, unique=True)
    git = db.Column(db.String(255), nullable=False, default='')
    branch = db.Column(db.String(255), nullable=False, default='')
    commit = db.Column(db.String(255), nullable=False, default='')

    specs_text = db.Column(db.Text)
    nickname = db.Column(db.String(64), nullable=False)
    comment = db.Column(db.Text)

    version = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(64), nullable=False, default='')

    users = db.relationship('User', secondary=job_user_association,
                            backref=db.backref('jobs', lazy='dynamic'), lazy='dynamic')

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

    def grant_user(self, user):
        if user.granted_to_job(self):
            return

        self.users.append(user)
        db.session.add(self)
        db.session.commit()

    def revoke_user(self, user):
        self.users.remove(user)
        db.session.add(self)
        db.session.commit()

    def list_users(self):
        return self.users.all()

    @cached_property
    def specs(self):
        dic = yaml.load(self.specs_text)
        return load_job_specs(dic)

    def delete(self):
        """
        the caller must ensure the all kubernetes objects have been deleted
        :return:
        """
        return super(Job, self).delete()


event.listen(
    Job.__table__,
    'after_create',
    DDL('ALTER TABLE %(table)s AUTO_INCREMENT = 10001;'),
)
