import pytest

from app.security import safe_redirect_target, verify_bootstrap_credentials


@pytest.mark.parametrize(
    "candidate",
    [
        "/",
        "/settings",
        "/login?next=%2Fsettings",
        "/публикации?tab=новые#top",
    ],
)
def test_safe_redirect_target_accepts_root_relative_urls(candidate: str) -> None:
    assert safe_redirect_target(candidate, "https://app.example/") == candidate


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        "",
        "settings",
        " https://evil.example/",
        "https://evil.example/",
        "https://app.example/settings",
        "javascript:alert(1)",
        "https://[invalid",
        "//evil.example/path",
        "///evil.example/path",
        r"\evil.example\path",
        r"/\evil.example/path",
        "/%5cevil.example/path",
        "/%2f%2fevil.example/path",
        "/%252f%252fevil.example/path",
        "/safe\r\nLocation: https://evil.example/",
        "/safe%0d%0aLocation:%20https://evil.example/",
        "/safe%250d%250aLocation:%20https://evil.example/",
    ],
)
def test_safe_redirect_target_rejects_ambiguous_or_external_urls(
    candidate: str | None,
) -> None:
    assert safe_redirect_target(candidate, "https://app.example/") is None


@pytest.mark.parametrize(
    "host_url",
    [
        None,
        "",
        "app.example",
        "ftp://app.example/",
        "https:///missing-host",
        "https://[invalid",
    ],
)
def test_safe_redirect_target_fails_closed_for_invalid_host(
    host_url: str | None,
) -> None:
    assert safe_redirect_target("/settings", host_url) is None


def test_verify_bootstrap_credentials_accepts_exact_match() -> None:
    assert verify_bootstrap_credentials(
        "admin",
        "correct horse",
        "admin",
        "correct horse",
    )


def test_verify_bootstrap_credentials_checks_password_after_username_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparisons: list[tuple[str, str]] = []

    def compare(left: str, right: str) -> bool:
        comparisons.append((left, right))
        return left == right

    monkeypatch.setattr("app.security.hmac.compare_digest", compare)

    assert not verify_bootstrap_credentials(
        "admin",
        "correct horse",
        "intruder",
        "correct horse",
    )
    assert comparisons == [
        ("admin", "intruder"),
        ("correct horse", "correct horse"),
    ]


@pytest.mark.parametrize(
    ("supplied_username", "supplied_password"),
    [
        ("Admin", "correct horse"),
        ("admin", "wrong"),
        ("Admin", "wrong"),
        ("", ""),
        (None, "correct horse"),
        ("admin", None),
    ],
)
def test_verify_bootstrap_credentials_rejects_mismatch(
    supplied_username: str | None,
    supplied_password: str | None,
) -> None:
    assert not verify_bootstrap_credentials(
        "admin",
        "correct horse",
        supplied_username,
        supplied_password,
    )


@pytest.mark.parametrize(
    ("configured_username", "configured_password"),
    [
        (None, "password"),
        ("admin", None),
        ("", "password"),
        ("admin", ""),
        ("   ", "password"),
        ("admin", "   "),
    ],
)
def test_verify_bootstrap_credentials_fails_closed_without_configuration(
    configured_username: str | None,
    configured_password: str | None,
) -> None:
    assert not verify_bootstrap_credentials(
        configured_username,
        configured_password,
        "admin",
        "password",
    )
