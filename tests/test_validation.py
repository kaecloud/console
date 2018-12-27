import pytest

from marshmallow import ValidationError

from console.libs.validation import (
    secret_schema, validate_appname, validate_tag, validate_git
)


# def test_secret():
#     dd = {
#         "data": {
#             "k1": "v1",
#             "k2": "v2",
#         }
#     }
#     secret_schema.load(dd)

def test_validate_appname():
    good_appnames = ['aaa', "AAA", 'aaa-bbb', 'a1-bbb', "aa_bb", "_"]
    bad_appnames = ['1a_aa', "aaa*bb", "aaa#bbb", "-"]
    for name in good_appnames:
        validate_appname(name)
    for name in bad_appnames:
        with pytest.raises(ValidationError):
            validate_appname(name)


def test_validate_tag():
    good_tags = ['aaa', "AAA", 'aaa-bbb', 'a1-bbb', "aa_bb", "_", "aa.bb"]
    bad_tags = ["aaa*bb", "aa#bbn"]
    for tag in good_tags:
        validate_tag(tag)
    for tag in bad_tags:
        with pytest.raises(ValidationError):
            validate_tag(tag)


def test_validate_git():
    good_git_urls = [
        'ssh://user@host.xz:port/path/to/repo.git/'
        'ssh://user@host.xz/path/to/repo.git/'
        'user@host.xz:/path/to/repo.git/',
        'git://host.xz/~user/path/to/repo.git/',
        'http://host.xz/path/to/repo.git/',
        'https://host.xz/path/to/repo.git/',
    ]
    for git_url in good_git_urls:
        validate_git(git_url)
