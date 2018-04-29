# -*- coding: utf-8 -*-

import yaml
from sqlalchemy import event, DDL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.utils import cached_property

from console.ext import db
from console.libs.exceptions import ModelDeleteError
from console.libs.utils import logger
from console.models.base import BaseModelMixin
from console.models.specs import specs_schema


class App(BaseModelMixin):
    name = db.Column(db.CHAR(64), nullable=False, unique=True)
    # 形如 git@gitlab.ricebook.net:platform/apollo.git
    git = db.Column(db.String(255), nullable=False)
    # {'prod': {'PASSWORD': 'xxx'}, 'test': {'PASSWORD': 'xxx'}}

    def __str__(self):
        return '<{}:{}>'.format(self.name, self.git)

    @classmethod
    def get_or_create(cls, name, git=None):
        app = cls.get_by_name(name)
        if app:
            return app

        app = cls(name=name, git=git)
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
                    AppUserRelation.filter_by(appname=self.name).all()]
        users = [User.get(id_) for id_ in user_ids]
        return users

    @property
    def latest_release(self):
        return Release.query.filter_by(app_id=self.id).order_by(Release.id.desc()).limit(1).first()

    @property
    def services(self):
        release = self.latest_release
        return release and release.services

    @property
    def specs(self):
        r = self.latest_release
        return r and r.specs

    @property
    def subscribers(self):
        specs = self.specs
        return specs and specs.subscribers

    def has_problematic_container(self, zone=None):
        containers = self.get_container_list(zone)
        if not containers or {c.status() for c in containers} == {'running'}:
            return False
        return True

    def delete(self):
        appname = self.name
        #TODO notify k8s to delete Deployment object

        # delete all deployments
        Release.query.filter_by(app_id=self.id).delete()
        # delete all permissions
        AppUserRelation.query.filter_by(appname=appname).delete()
        return super(App, self).delete()


class Release(BaseModelMixin):
    __table_args__ = (
        db.UniqueConstraint('app_id', 'sha'),
    )

    sha = db.Column(db.CHAR(64), nullable=False, index=True)
    app_id = db.Column(db.Integer, nullable=False)
    image = db.Column(db.String(255), nullable=False, default='')
    specs_text = db.Column(db.JSON)
    # store trivial info like branch, author, git tag, commit messages
    misc = db.Column(db.JSON)

    def __str__(self):
        return '<{r.appname}:{r.short_sha}>'.format(r=self)

    @classmethod
    def create(cls, app, sha, specs_text=None, branch='', git_tag='', author='', commit_message='', git=''):
        """app must be an App instance"""
        appname = app.name

        unmarshal_result = specs_schema.load(yaml.load(specs_text))
        misc = {
            'git_tag': git_tag,
            'author': author,
            'commit_message': commit_message,
        }

        try:
            new_release = cls(sha=sha, app_id=app.id, specs_text=specs_text, misc=misc)
            db.session.add(new_release)
            db.session.commit()
        except IntegrityError:
            logger.warn('Fail to create Release %s %s, duplicate', appname, sha)
            db.session.rollback()
            raise

        return new_release

    def delete(self):
        container_list = self.get_container_list()
        if container_list:
            raise ModelDeleteError('Release {} is still running, delete containers {} before deleting this release'.format(self.short_sha, container_list))
        logger.warn('Deleting release %s', self)
        Deployment.query.filter_by(release_id=self.id).delete()
        return super(Release, self).delete()

    @classmethod
    def get(cls, id):
        r = super(Release, cls).get(id)
        # 要检查下 app 还在不在, 不在就失败吧
        if r and r.app:
            return r
        return None

    @classmethod
    def get_by_app(cls, name, start=0, limit=None):
        app = App.get_by_name(name)
        if not app:
            return []

        q = cls.query.filter_by(app_id=app.id).order_by(cls.id.desc())
        return q[start:start + limit]

    @classmethod
    def get_by_app_and_sha(cls, name, sha):
        app = App.get_by_name(name)
        if not app:
            raise ValueError('app {} not found'.format(name))

        if len(sha) < 7:
            raise ValueError('minimum sha length is 7')
        return cls.query.filter(cls.app_id==app.id, cls.sha.like('{}%'.format(sha))).first()

    @property
    def raw(self):
        """if no builds clause in app.yaml, this release is considered raw"""
        return not self.specs.stages

    @property
    def short_sha(self):
        return self.sha[:7]

    @property
    def app(self):
        return App.get(self.app_id)

    @property
    def appname(self):
        return self.app.name

    @property
    def git_tag(self):
        return self.misc.get('git_tag')

    @property
    def commit_message(self):
        return self.misc.get('commit_message')

    @property
    def author(self):
        return self.misc.get('author')

    @property
    def git(self):
        return self.misc.get('git')

    @cached_property
    def specs(self):
        dic = yaml.load(self.specs_text)
        unmarshal_result = specs_schema.load(dic)
        return unmarshal_result.data

    @property
    def services(self):
        return self.specs.services

    def update_image(self, image):
        try:
            self.image = image
            logger.debug('Set image %s for release %s', image, self.sha)
            db.session.add(self)
            db.session.commit()
        except StaleDataError:
            db.session.rollback()


class Deployment(BaseModelMixin):
    release_id = db.Column(db.Integer, nullable=False)
    service_name = db.Column(db.CHAR(64), nullable=False)
    specs_text = db.Column(db.JSON)
    debug = db.Column(db.Integer, default=0)

    def __str__(self):
        return '<{} deployment:{}>'.format(self.appname, self.name)

    @classmethod
    def create(cls, release, service_name=None, specs_text=None, debug=None):
        try:
            dp = cls(release_id=release.id, service_name=service_name, specs_text=specs_text, debug=debug)
            db.session.add(dp)
            db.session.commit()
            return dp
        except IntegrityError:
            db.session.rollback()
            raise

    @property
    def release(self):
        return Release.get(self.release_id)

    @property
    def app(self):
        return App.get(self.app_id)

    @property
    def appname(self):
        return self.release.appname


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
