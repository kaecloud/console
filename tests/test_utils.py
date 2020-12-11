import pytest

from console.libs.utils import (
    validate_release_version
)


def test_validate_release_version():
    good_vers = {
        "v1.2.3", "v1.2.3-alpha", "1.2.3",
        "20.12.30.60", "20.01.01.01",
    }
    bad_vers = [
        "v1.01.3", "v01.1.3",
        "20.13.30.60", "20.12.31.60", "ttt.1.2",
    ]
    for v in good_vers:
        assert validate_release_version(v) is True
    for v in bad_vers:
        assert validate_release_version(v) is False
