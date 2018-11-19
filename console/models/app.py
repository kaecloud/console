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


app_user_association = db.Table('app_user_association',
    db.Column('app_id', db.Integer, db.ForeignKey('app.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
)


class App(BaseModelMixin):
    __tablename__ = "app"
    name = db.Column(db.CHAR(64), nullable=False, unique=True)
    # 形如 git@gitlab.ricebook.net:platform/apollo.git
    git = db.Column(db.String(255), nullable=False)
    type = db.Column(db.CHAR(64), nullable=False)
    users = db.relationship('User', secondary=app_user_association,
                            backref=db.backref('apps', lazy='dynamic'))

    def __str__(self):
        return self.name

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

    def grant_user(self, user):
        if user.granted_to_app(self):
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
        return super(App, self).delete()


class Release(BaseModelMixin):
    __table_args__ = (
        db.UniqueConstraint('app_id', 'tag'),
    )
    # git tag
    tag = db.Column(db.CHAR(64), nullable=False, index=True)
    # TODO use ForeignKey
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

    def get_previous_version(self, n=0):
        q_set = Release.query.filter(Release.app_id == self.app_id, Release.id < self.id).order_by(Release.id.desc()).limit(n+1).all()
        if len(q_set) <= n:
            return None
        else:
            return q_set[n]

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

    @cached_property
    def specs_dict(self):
        return yaml.load(self.specs_text)

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


class AppYaml(BaseModelMixin):
    __table_args__ = (
        db.UniqueConstraint('app_id', 'name'),
    )
    # git tag
    name = db.Column(db.CHAR(64), nullable=False, index=True)
    app_id = db.Column(db.Integer, nullable=False)
    specs_text = db.Column(db.Text)
    comment = db.Column(db.Text)

    def __str__(self):
        return '<{r.appname}:{r.name}>'.format(r=self)

    @classmethod
    def create(cls, name, app, specs_text, comment=''):
        """app must be an App instance"""
        appname = app.name

        # check the format of specs text(ignore the result)
        app_specs_schema.load(yaml.load(specs_text))

        try:
            new_yaml = cls(name=name, app_id=app.id, specs_text=specs_text, comment=comment)
            db.session.add(new_yaml)
            db.session.commit()
        except IntegrityError:
            logger.warn('Fail to create AppYaml %s %s, duplicate', appname, name)
            db.session.rollback()
            raise

        return new_yaml

    @classmethod
    def get_by_app_and_name(cls, app, name):
        return cls.query.filter_by(app_id=app.id, name=name).first()

    @classmethod
    def get_by_app(cls, app, start=0, limit=10):
        q = cls.query.filter_by(app_id=app.id).order_by(cls.id.desc())
        return q[start:start + limit]

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


class SpecVersion(BaseModelMixin):
    # TODO use ForeignKey
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

    @classmethod
    def get_newest_version_by_tag_app(cls, app_id, tag):
        return SpecVersion.query.filter_by(app_id=app_id, tag=tag).order_by(SpecVersion.id.desc()).first()

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


event.listen(
    App.__table__,
    'after_create',
    DDL('ALTER TABLE %(table)s AUTO_INCREMENT = 10001;'),
)
