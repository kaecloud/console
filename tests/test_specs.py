# -*- coding: utf-8 -*-

import pytest
from marshmallow import ValidationError

from .prepare import default_builds, default_hook, make_specs, default_appname, default_entrypoints, default_publish, default_sha, default_combo_name, healthcheck_http_url


def test_extra_fields():
    # do not allow unknown fields
    # for example, a typo
    with pytest.raises(ValidationError):
        make_specs(typo_field='whatever')


def test_entrypoints():
    # no underscore in entrypoint_name
    bad_entrypoints = default_entrypoints.copy()
    bad_entrypoints['web_prod'] = bad_entrypoints.pop('web')
    with pytest.raises(ValidationError):
        make_specs(entrypoints=bad_entrypoints)

    # validate ports parsing
    specs = make_specs()
    entrypoints = specs['entrypoints']
    publish = entrypoints['web'].publish
    assert list(publish) == default_publish
    assert entrypoints['web'].command == 'python -m http.server'
    assert entrypoints['web'].working_dir == '/home/{}'.format(default_appname)
    assert entrypoints['test-working-dir'].command == 'echo pass'
    assert entrypoints['test-working-dir'].working_dir == '/tmp'


def test_build():
    default_base = 'bar'
    default_builds = {
        'first': {
            'commands': ['echo whatever'],
        },
        'final': {
            'base': 'foo',
            'commands': ['echo whatever'],
        },
    }
    specs = make_specs(base=default_base,
                       stages=list(default_builds.keys()),
                       builds=default_builds)
    builds = specs['builds']
    assert builds['first']['base'] == default_base
    assert builds['final']['base'] == 'foo'

    with pytest.raises(ValidationError) as exc:
        make_specs(base=None,
                   stages=list(default_builds.keys()),
                   builds=default_builds)

    assert 'either use a global base image as default build base, or specify base in each build stage' in str(exc)

    with pytest.raises(ValidationError) as exc:
        make_specs(stages=['wrong-stage-name'])

    assert 'stages inconsistent with' in str(exc)

    with pytest.raises(ValidationError) as exc:
        make_specs(container_user='should-not-be-here')

    assert 'cannot specify container_user because this release is not raw' in str(exc)


def test_healthcheck():
    entrypoints = {
        'default-healthcheck': {
            'cmd': 'python -m http.server',
            'ports': default_publish,
        },
        'http-healthcheck': {
            'cmd': 'python -m http.server',
            'ports': default_publish,
            'healthcheck': {
                'http_url': healthcheck_http_url,
                'http_port': default_publish[0],
                'http_code': 200,
            }
        },
        'http-partial-healthcheck': {
            'cmd': 'python -m http.server',
            'ports': default_publish,
            'healthcheck': {
                'http_url': healthcheck_http_url,
                'http_port': default_publish[0],
            }
        },
    }
    specs = make_specs(entrypoints=entrypoints)
    default_healthcheck = specs['entrypoints']['default-healthcheck'].healthcheck
    assert list(default_healthcheck.tcp_ports) == default_publish
    assert not default_healthcheck.http_url
    assert not default_healthcheck.http_port
    assert not default_healthcheck.http_code

    http_healthcheck = specs['entrypoints']['http-healthcheck'].healthcheck
    assert http_healthcheck.http_port == int(default_publish[0])
    assert http_healthcheck.http_code == 200

    http_partial_healthcheck = specs['entrypoints']['http-partial-healthcheck'].healthcheck
    assert http_partial_healthcheck.http_code == 200

    # if use http health check, must define all three variables
    bad_entrypoints = {
        'http-healthcheck': {
            'cmd': 'python -m http.server',
            'ports': default_publish,
            'healthcheck': {
                # missing http_port and http_code
                'http_url': '/healthcheck',
            }
        },
    }
    with pytest.raises(ValidationError) as e:
        make_specs(entrypoints=bad_entrypoints)

    assert 'If you plan to use HTTP health check, you must define (at least) http_port, http_url' in str(e)

    specs = make_specs(erection_timeout=0)
    assert specs['erection_timeout'] == 0


