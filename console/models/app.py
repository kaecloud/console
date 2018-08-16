# -*- coding: utf-8 -*-

import json
import yaml
from addict import Dict
from sqlalchemy import event, DDL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.utils import cached_property

from console.ext import db
from console.libs.utils import logger
from console.models.base import BaseModelMixin
from console.models.specs import app_specs_schema


class App(BaseModelMixin):
    name = db.Column(db.CHAR(64), nullable=False, unique=True)
    # 形如 git@gitlab.ricebook.net:platform/apollo.git
    git = db.Column(db.String(255), nullable=False)
    type = db.Column(db.CHAR(64), nullable=False)

    def __str__(self):
        return '<{}:{}>'.format(self.name, self.git)

    @classmethod
    def get_or_create(cls, name, git, apptype):
        app = cls.get_by_name(name)
        if app:
            return app

        app = cls(name=name, git=git, type=apptype)
        db.session.add(app)
        db.session.commit()
        return app

    @classmethod
    def get_by_name(cls, name):
        return cls.query.filter_by(name=name).first()

    @classmethod
    def get_by_user(cls, user_id):
        """拿这个user可以有的app, 跟app自己的user_id没关系."""
        names = AppUserRelation.get_appname_by_user_id(user_id)
        return [cls.get_by_name(n) for n in names]

    def grant_user(self, user):
        AppUserRelation.create(self, user)

    def revoke_user(self, user):
        AppUserRelation.query.filter_by(appname=self.name, user_id=user.id).delete()
        db.session.commit()

    def list_users(self):
        from console.models.user import User
        user_ids = [r.user_id for r in
                    AppUserRelation.query.filter_by(appname=self.name).all()]
        users = [User.get(id_) for id_ in user_ids]
        return users

    @property
    def latest_release(self):
        return Release.query.filter_by(app_id=self.id).order_by(Release.id.desc()).limit(1).first()

    def get_release_by_tag(self, tag):
        return Release.query.filter_by(app_id=self.id, tag=tag).first()

    @property
    def service(self):
        release = self.latest_release
        return release and release.service

    @property
    def specs(self):
        r = self.latest_release
        return r and r.specs

    @property
    def subscribers(self):
        specs = self.specs
        return specs and specs.subscribers

    def delete(self):
        """
        the caller must ensure the all kubernetes objects have been deleted
        :return:
        """
        appname = self.name

        # delete all releases
        Release.query.filter_by(app_id=self.id).delete()
        SpecVersion.query.filter_by(app_id=self.id).delete()
        # delete all permissions
        AppUserRelation.query.filter_by(appname=appname).delete()
        return super(App, self).delete()


class Release(BaseModelMixin):
    __table_args__ = (
        db.UniqueConstraint('app_id', 'tag'),
    )
    # git tag
    tag = db.Column(db.CHAR(64), nullable=False, index=True)
    app_id = db.Column(db.Integer, nullable=False)
    image = db.Column(db.CHAR(255), nullable=False, default='')
    build_status = db.Column(db.Boolean, nullable=False, default=False)
    specs_text = db.Column(db.Text)
    # store trivial info like branch, author, git tag, commit messages
    misc = db.Column(db.Text)

    def __str__(self):
        return '<{r.appname}:{r.tag}>'.format(r=self)

    @classmethod
    def create(cls, app, tag, specs_text, image=None, build_status=False, branch='', author='', commit_message=''):
        """app must be an App instance"""
        appname = app.name

        # check the format of specs text(ignore the result)
        app_specs_schema.load(yaml.load(specs_text))
        misc = {
            'author': author,
            'commit_message': commit_message,
            'git': app.git,
        }

        try:
            new_release = cls(tag=tag, app_id=app.id, image=image, build_status=build_status, specs_text=specs_text, misc=json.dumps(misc))
            db.session.add(new_release)
            db.session.commit()
        except IntegrityError:
            logger.warn('Fail to create Release %s %s, duplicate', appname, tag)
            db.session.rollback()
            raise

        return new_release

    def update(self, specs_text, image=None, build_status=False, branch='', author='', commit_message=''):
        """app must be an App instance"""
        # check the format of specs text(ignore the result)
        app_specs_schema.load(yaml.load(specs_text))
        misc = {
            'author': author,
            'commit_message': commit_message,
            'git': self.git,
        }

        try:
            # self.specs_text = specs_text
            super(Release, self).update(specs_text=specs_text, image=image, build_status=build_status, misc=json.dumps(misc))
        except:
            logger.warn('Fail to update Release %s %s', self.appname, self.tag)
            db.session.rollback()
            # raise
        return self

    def delete(self):
        logger.warn('Deleting release %s', self)
        return super(Release, self).delete()

    @classmethod
    def get(cls, id):
        r = super(Release, cls).get(id)
        if r and r.app:
            return r
        return None

    @classmethod
    def get_by_app(cls, name, start=0, limit=100):
        app = App.get_by_name(name)
        if not app:
            return []

        q = cls.query.filter_by(app_id=app.id).order_by(cls.id.desc())
        return q[start:start + limit]

    @classmethod
    def get_by_app_and_tag(cls, name, tag):
        app = App.get_by_name(name)
        if not app:
            raise ValueError('app {} not found'.format(name))

        return cls.query.filter_by(app_id=app.id, tag=tag).first()

    @property
    def raw(self):
        """if no builds clause in app.yaml, this release is considered raw"""
        return not self.specs.builds

    @property
    def app(self):
        return App.get(self.app_id)

    @property
    def appname(self):
        return self.app.name

    @property
    def commit_message(self):
        misc = json.loads(self.misc)
        return misc.get('commit_message')

    @property
    def author(self):
        misc = json.loads(self.misc)
        return misc.get('author')

    @property
    def git(self):
        misc = json.loads(self.misc)
        return misc.get('git')

    @cached_property
    def specs(self):
        dic = yaml.load(self.specs_text)
        unmarshal_result = app_specs_schema.load(dic)
        return unmarshal_result.data

    @property
    def service(self):
        return self.specs.service

    def update_build_status(self, status):
        try:
            self.build_status = status
            db.session.add(self)
            db.session.commit()
        except StaleDataError:
            db.session.rollback()


class SpecVersion(BaseModelMixin):
    # git tag
    tag = db.Column(db.CHAR(64), nullable=False, index=True)
    app_id = db.Column(db.Integer, nullable=False)
    specs_text = db.Column(db.Text)

    def __str__(self):
        return 'SpecVersion <{r.appname}:{r.tag}:{r.id}>'.format(r=self)

    @classmethod
    def create(cls, app, tag, specs_text):
        """app must be an App instance"""
        if isinstance(specs_text, Dict):
            specs_text = yaml.dump(specs_text.to_dict())
        elif isinstance(specs_text, dict):
            specs_text = yaml.dump(specs_text)
        else:
            # check the format of specs text(ignore the result)
            app_specs_schema.load(yaml.load(specs_text))

        try:
            new_release = cls(tag=tag, app_id=app.id, specs_text=specs_text)
            db.session.add(new_release)
            db.session.commit()
        except IntegrityError:
            logger.warn('Fail to create SpecVersion %s %s, duplicate', app.name, tag)
            db.session.rollback()
            raise

        return new_release

    def delete(self):
        logger.warn('Deleting release %s', self)
        return super(SpecVersion, self).delete()

    @classmethod
    def get(cls, id):
        r = super(SpecVersion, cls).get(id)
        if r and r.app:
            return r
        return None

    @classmethod
    def get_by_app(cls, app, start=0, limit=None):
        q = cls.query.filter_by(app_id=app.id).order_by(cls.id.desc())
        if limit is None:
            return q[start:]
        else:
            return q[start:start + limit]

    def get_previous_version(self, n=0):
        q_set = SpecVersion.query.filter(SpecVersion.id < self.id).order_by(SpecVersion.id.desc()).limit(n+1).all()
        if len(q_set) <= n:
            return None
        else:
            return q_set[n]

    @property
    def release(self):
        return Release.query.filter_by(app_id=self.app_id, tag=self.tag).first()

    @property
    def app(self):
        return App.get(self.app_id)

    @property
    def appname(self):
        return self.app.name

    @cached_property
    def specs(self):
        dic = yaml.load(self.specs_text)
        unmarshal_result = app_specs_schema.load(dic)
        return unmarshal_result.data


class AppUserRelation(BaseModelMixin):
    __table_args__ = (
        db.UniqueConstraint('user_id', 'appname'),
    )

    appname = db.Column(db.CHAR(64), nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False)

    @classmethod
    def create(cls, app, user):
        relation = cls(appname=app.name, user_id=user.id)
        try:
            db.session.add(relation)
            db.session.commit()
            return relation
        except IntegrityError:
            db.session.rollback()
            raise


event.listen(
    App.__table__,
    'after_create',
    DDL('ALTER TABLE %(table)s AUTO_INCREMENT = 10001;'),
)
