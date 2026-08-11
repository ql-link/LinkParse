import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_api_keys_accept_comma_separated_environment(monkeypatch):
    monkeypatch.setenv("LINKPARSE_API_KEYS", "alpha,beta")
    assert Settings().api_keys == ["alpha", "beta"]


def test_opendataloader_table_method_is_normalized():
    assert Settings(opendataloader_table_method="CLUSTER").opendataloader_table_method == "cluster"


def test_opendataloader_table_method_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Settings(opendataloader_table_method="fast")
