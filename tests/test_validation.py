import pytest

from marshmallow import ValidationError

from console.libs.validation import (
    validate_git,
)


# def test_secret():
#     dd = {
#         "data": {
#             "k1": "v1",
#             "k2": "v2",
#         }
#     }
#     secret_schema.load(dd)

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
