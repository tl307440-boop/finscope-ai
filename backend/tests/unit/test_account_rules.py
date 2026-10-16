from typing import cast

import pytest
from app.core.exceptions import AppError
from app.core.security import PasswordService
from app.repositories.accounts import AccountRepository
from app.services.accounts import bootstrap_admin, clean_identity, password_problem


class FakeUsers:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = existing or set()
        self.created: list[dict[str, str]] = []

    def get_by_username(self, username: str) -> object | None:
        return object() if username in self.existing else None

    def create_user(self, *, username: str, email: str, password_hash: str, role: str) -> None:
        self.created.append({"username": username, "email": email, "role": role})


def test_password_policy() -> None:
    assert password_problem("alice", "short1") is not None
    assert password_problem("alice", "onlyletters") is not None
    assert password_problem("alice", "123456789012") is not None
    assert password_problem("alice", "alice12345".replace("12345", "")) is not None
    assert password_problem("alice1234", "Alice1234") is not None
    assert password_problem("alice", "password1") is not None
    assert password_problem("alice", "x" * 200) is not None
    assert password_problem("alice", "correct-horse-9") is None


def test_identity_is_normalised_and_validated() -> None:
    assert clean_identity("  Alice_01 ", " Alice@Example.COM ") == ("alice_01", "alice@example.com")
    for username, email in (
        ("ab", "a@b.co"),
        ("has space", "a@b.co"),
        ("中文账号", "a@b.co"),
        ("alice", "not-an-email"),
        ("alice", "a@b"),
    ):
        with pytest.raises(AppError):
            clean_identity(username, email)


def _boot(users: FakeUsers, username: str, email: str, password: str) -> str:
    return bootstrap_admin(
        cast(AccountRepository, users),
        PasswordService("test"),
        username=username,
        email=email,
        password=password,
    )


def test_bootstrap_admin_outcomes() -> None:
    users = FakeUsers()
    assert _boot(users, "", "", "") == "skipped"
    assert _boot(users, "boss", "boss@x.io", "weak") == "weak"
    assert _boot(users, "b", "boss@x.io", "strong-pass-1") == "invalid"
    assert users.created == []
    assert _boot(users, "Boss", "boss@x.io", "strong-pass-1") == "created"
    assert users.created == [{"username": "boss", "email": "boss@x.io", "role": "admin"}]
    taken = FakeUsers({"boss"})
    assert _boot(taken, "boss", "boss@x.io", "strong-pass-1") == "exists"
    assert taken.created == []
