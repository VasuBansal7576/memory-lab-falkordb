import pytest


@pytest.fixture(autouse=True)
def keyword_only_unless_explicitly_testing_semantics(monkeypatch):
    # The real embedding path has a dedicated integration test. Most persistence
    # tests need no model inference or model download for each graph revision.
    monkeypatch.setenv('MEMORY_SEMANTIC','0')
