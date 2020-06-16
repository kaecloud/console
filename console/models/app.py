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
from kaelib.spec import app_specs_schema


class App(BaseModelMixin):
    __tablename__ = "app"
    name = db.Column(db.CHAR(64), nullable=False, unique=True)
    git = db.Column(db.String(255), nullable=False)
    type = db.Column(db.CHAR(64), nullable=False)
    subscribers = db.Column(db.Text())

    def __str__(self):
        return self.name

    @classmethod
    def get_or_create(cls, name, git, apptype, subscribers=None):
        app = cls.get_by_name(name)
        if app:
            return app
        return cls.create(name, git, apptype, subscribers)

    @classmethod
    def create(cls, name, git, apptype, subscribers=None):
        subscriber_names = None
        if subscribers is not None:
            subscriber_names = json.dumps([u.username for u in subscribers])
        app = cls(name=name, git=git, type=apptype, subscribers=subscriber_names)
        db.session.add(app)
        db.session.commit()
        return app

    @classmethod
    def get_by_name(cls, name):
        return cls.query.filter_by(name=name).first()

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
    def subscriber_list(self):
        if not self.subscribers:
            return []
        try:
            username_list = json.loads(self.subscribers)
        except json.JSONDecodeError as e:
            logger.exception(f"subscribers of app {self.name} is invalid({self.subscribers})")
            return []

        from console.models.user import User
        users = []
        for username in username_list:
            user = User.get_by_username(username)
            if user is not None:
                users.append(user)
        return users

    def delete(self):
        """
        the caller must ensure the all kubernetes objects have been deleted
        :return:
        """
        # delete all releases
        Release.query.filter_by(app_id=self.id).delete()
        DeployVersion.query.filter_by(app_id=self.id).delete()
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


class DeployVersion(BaseModelMixin):
    # TODO use ForeignKey
    # git tag
    tag = db.Column(db.CHAR(64), nullable=False, index=True)
    app_id = db.Column(db.Integer, nullable=False)
    parent_id = db.Column(db.Integer, nullable=False)
    cluster = db.Column(db.CHAR(64), nullable=False)
    config_id = db.Column(db.Integer)
    specs_text = db.Column(db.Text)

    def __str__(self):
        return 'DeployVersion <{r.appname}:{r.tag}:{r.id}>'.format(r=self)

    @classmethod
    def create(cls, app, tag, specs_text, parent_id, cluster, config_id=None):
        """app must be an App instance"""
        if isinstance(specs_text, Dict):
            specs_text = yaml.dump(specs_text.to_dict())
        elif isinstance(specs_text, dict):
            specs_text = yaml.dump(specs_text)
        else:
            # check the format of specs text(ignore the result)
            app_specs_schema.load(yaml.load(specs_text))

        try:
            ver = cls(tag=tag, app_id=app.id, parent_id=parent_id, cluster=cluster, config_id=config_id, specs_text=specs_text)
            db.session.add(ver)
            db.session.commit()
        except IntegrityError:
            logger.warn('Fail to create SpecVersion %s %s, duplicate', app.name, tag)
            db.session.rollback()
            raise

        return ver

    def delete(self):
        logger.warn('Deleting DeployVersion %s', self)
        return super(DeployVersion, self).delete()

    @classmethod
    def get(cls, id):
        if isinstance(id, str):
            id = int(id)
        r = super(DeployVersion, cls).get(id)
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
    def get_previous_version(cls, cur_id, revision):
        while revision >= 0:
            ver = cls.get(id=cur_id)
            cur_id = ver.parent_id
            revision -= 1
        return cls.get(id=cur_id)

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

    @cached_property
    def app_config(self):
        if self.config_id is None:
            return None
        return AppConfig.get(self.config_id)

    def to_k8s_annotation(self):
        return {
            'deploy_id': self.id,
            'app_id': self.app_id,
            'release_tag': self.tag,
            'config_id': self.config_id,
            'cluster': self.cluster,
        }


class AppConfig(BaseModelMixin):
    app_id = db.Column(db.Integer, nullable=False)
    cluster = db.Column(db.CHAR(64), nullable=False)
    content = db.Column(db.Text)
    comment = db.Column(db.Text)

    def __str__(self):
        return '<{r.appname}:{r.name}>'.format(r=self)

    @classmethod
    def create(cls, app, cluster, content, comment=''):
        """app must be an App instance"""
        appname = app.name
        if isinstance(content, dict):
            content = json.dumps(content)

        try:
            new_cfg = cls(app_id=app.id, cluster=cluster, content=content, comment=comment)
            db.session.add(new_cfg)
            db.session.commit()
        except IntegrityError:
            logger.warn('Fail to create AppConfig %s, duplicate', appname)
            db.session.rollback()
            raise

        return new_cfg

    @classmethod
    def get_by_app_and_cluster(cls, app, cluster, start=0, limit=10):
        q = cls.query.filter_by(app_id=app.id, cluster=cluster).order_by(cls.id.desc())
        return q[start:start + limit]

    @classmethod
    def get_newest_config(cls, app, cluster):
        q = cls.query.filter_by(app_id=app.id, cluster=cluster).order_by(cls.id.desc())
        return q.first()

    @property
    def app(self):
        return App.get(self.app_id)

    @property
    def appname(self):
        return self.app.name

    @cached_property
    def data_dict(self):
        return json.loads(self.content)


event.listen(
    App.__table__,
    'after_create',
    DDL('ALTER TABLE %(table)s AUTO_INCREMENT = 10001;'),
)
