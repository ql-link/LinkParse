from app.core.config import Settings


def test_api_keys_accept_comma_separated_environment(monkeypatch):
    monkeypatch.setenv("LINKPARSE_API_KEYS", "alpha,beta")
    assert Settings().api_keys == ["alpha", "beta"]
