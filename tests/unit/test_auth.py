import hashlib

import pytest

from app.security.auth import hash_token, new_token, parse_bearer


def test_tokens_are_random_and_long():
    a, b = new_token(), new_token()
    assert a != b
    assert len(a) >= 43


def test_hash_is_sha256_hex_and_does_not_contain_token():
    token = "demo-token"
    assert hash_token(token) == hashlib.sha256(b"demo-token").hexdigest()
    assert token not in hash_token(token)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc ", "abc"),
        ("Basic abc", None),
        ("Bearer ", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_bearer(header, expected):
    assert parse_bearer(header) == expected
