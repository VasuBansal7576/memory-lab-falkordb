import json

import httpx
import pytest

from memory_lab.generation import GenerationError, generate


@pytest.fixture
def evidence():
    return {"status": "supported", "question": "Who leads Platform?", "answer": "Elena leads Platform [S1].",
            "paths": [], "citations": [{"id": "S1", "quote": "Elena leads Platform."}]}


def test_missing_evidence_does_not_call_a_model():
    assert generate({"status": "missing"}) == {"status": "missing"}


def test_requires_explicit_model_configuration(evidence, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(GenerationError, match="locally"):
        generate(evidence)


@pytest.mark.parametrize("answer,ids,accepted", [
    ("Elena leads Platform [S1].", ["S1"], True),
    ("Elena leads Platform [S9].", ["S9"], False),
    ("Elena leads Platform.", ["S1"], False),
    ("Elena leads Platform [S1].", ["S1", "S2"], False),
])
def test_provider_citations_and_actual_usage(evidence, monkeypatch, answer, ids, accepted):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")

    def handle(request):
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert "Elena leads Platform." in payload["messages"][0]["content"]
        return httpx.Response(200, json={"model": "resolved-test-model", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps({"answer": answer, "citation_ids": ids})}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 30, "output_tokens": 5}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        if accepted:
            result = generate(evidence, client)
            assert result["usage"]["total_input_tokens"] == 60
            assert result["model"] == "resolved-test-model"
        else:
            with pytest.raises(GenerationError, match="citations"):
                generate(evidence, client)


def test_truncated_model_response_is_not_accepted(evidence, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"stop_reason": "max_tokens"}))) as client:
        with pytest.raises(GenerationError, match="partial"):
            generate(evidence, client)


def test_codex_uses_compact_evidence_without_computed_answer(evidence, monkeypatch):
    from memory_lab.generation import generate_codex
    monkeypatch.setattr('memory_lab.generation.codex_configuration', lambda: {'configured': True, 'model': 'test-model'})
    evidence['answer'] = 'SECRET PRECOMPUTED ANSWER'
    evidence['status'] = 'recalled'
    def run(prompt, schema, model, timeout, directory):
        assert 'SECRET' not in prompt
        assert 'Elena leads Platform.' in prompt
        assert 'Apply dates' in prompt
        events = [{'type': 'item.completed', 'item': {'type': 'agent_message',
            'text': json.dumps({'answer': 'Elena leads Platform [S1].', 'citation_ids': ['S1']})}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 60, 'output_tokens': 20}}]
        return {'returncode': 0, 'stdout': '\n'.join(json.dumps(e) for e in events)}
    monkeypatch.setattr('memory_lab.decisions.run_model', run)
    result = generate_codex(evidence)
    assert result['mode'] == 'codex'
    assert result['usage']['total_input_tokens'] == 100
    assert result['usage']['cached_input_tokens'] == 60


def test_codex_rejects_unknown_citations(evidence, monkeypatch):
    from memory_lab.generation import generate_codex
    monkeypatch.setattr('memory_lab.generation.codex_configuration', lambda: {'configured': True, 'model': 'test-model'})
    events = [{'type': 'item.completed', 'item': {'type': 'agent_message',
        'text': json.dumps({'answer': 'Elena [S9].', 'citation_ids': ['S9']})}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 0, 'output_tokens': 20}}]
    monkeypatch.setattr('memory_lab.decisions.run_model', lambda *args: {'returncode': 0, 'stdout': '\n'.join(json.dumps(e) for e in events)})
    with pytest.raises(GenerationError, match='citations'):
        generate_codex(evidence)
