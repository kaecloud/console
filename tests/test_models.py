# -*- coding: utf-8 -*-

import pytest
from datetime import datetime, timedelta

from .prepare import default_appname, default_sha
from console.config import FAKE_USER
from console.models.oplog import OPLog, OPType


def test_oplog(test_db):
    container_id = '4d14feae57cde3762bdf5efb7dd643a3fdb9421085d0c867de2f11cf399effea'
    create_at = datetime.now() - timedelta(seconds=1)
    OPLog.create(user_id=FAKE_USER['id'],
                 appname=default_appname,
                 sha=default_sha,
                 action=OPType.CREATE_CONTAINER,
                 content={'foo': 'bar'})
    OPLog.create(user_id=FAKE_USER['id'],
                 appname=default_appname,
                 sha=default_sha,
                 action=OPType.REMOVE_CONTAINER,
                 content={'foo': 'bar'})

    query_by_container_id = OPLog.get_by(container_id=container_id[:7])
    assert len(query_by_container_id) == 2
    query_by_sha = OPLog.get_by(sha=default_sha[:7])
    assert len(query_by_sha) == 2
    with pytest.raises(ValueError):
        OPLog.get_by(sha=default_sha[:3])

    query_by_time_window = OPLog.get_by(time_window=(create_at, datetime.now()))
    assert len(query_by_time_window) == 2
    query_by_false_time_window = OPLog.get_by(time_window=(None, create_at))
    assert not query_by_false_time_window

    query_all = OPLog.get_by()
    assert len(query_all) == 2
    query_with_limit = OPLog.get_by(limit=1)
    assert len(query_with_limit) == 1

    query_by_appname = OPLog.get_by(appname=default_appname)
    assert len(query_by_appname) == 2
