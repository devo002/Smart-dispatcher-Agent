"""Semantic triage cache, triage_node parsing (fake Claude), and graph routing edges."""

from types import SimpleNamespace

import pytest

from src.empire_dispatcher.graph import nodes


# --- _cosine ---------------------------------------------------------------

def test_cosine_identical_vectors_is_one():
    assert nodes._cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors_is_zero():
    assert nodes._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_does_not_divide_by_zero():
    assert nodes._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# --- cache get/put ---------------------------------------------------------

@pytest.fixture
def cache(monkeypatch):
    """Fresh empty cache + a deterministic embedder keyed by a lookup table."""
    monkeypatch.setattr(nodes, "_TRIAGE_CACHE", [])
    vectors = {}

    def embed(texts):
        return [vectors[t] for t in texts]

    monkeypatch.setattr(nodes, "_embed_fn", embed)
    return vectors


def test_empty_cache_returns_none(cache):
    assert nodes._cache_get("anything") is None


def test_identical_text_is_a_hit(cache):
    cache["a"] = [1.0, 0.0]
    nodes._cache_put("a", {"severity": "high"})
    assert nodes._cache_get("a") == {"severity": "high"}


def test_similarity_at_or_above_threshold_is_a_hit(cache):
    cache["a"] = [1.0, 0.0]
    cache["b"] = [1.0, 0.1]  # cosine ~0.995
    nodes._cache_put("a", {"severity": "high"})
    assert nodes._cache_get("b") == {"severity": "high"}


def test_similarity_below_threshold_is_a_miss(cache):
    cache["a"] = [1.0, 0.0]
    cache["b"] = [1.0, 1.0]  # cosine ~0.707
    nodes._cache_put("a", {"severity": "high"})
    assert nodes._cache_get("b") is None


def test_cache_evicts_oldest_when_full(cache, monkeypatch):
    monkeypatch.setattr(nodes, "_CACHE_MAX", 2)
    for i, name in enumerate(["a", "b", "c"]):
        cache[name] = [float(i + 1), 1.0]
        nodes._cache_put(name, {"id": name})
    assert [r["id"] for _, r in nodes._TRIAGE_CACHE] == ["b", "c"]


# --- triage_node -----------------------------------------------------------

def _state(**kw):
    base = dict(ticket_id="T-1", customer_name="A", customer_region="Berlin",
                raw_subject="Inverter down", raw_body="Error 0x0001 on my inverter")
    base.update(kw)
    return base


def _fake_claude(blocks):
    calls = {"n": 0, "kwargs": None}

    class Messages:
        def create(self, **kwargs):
            calls["n"] += 1
            calls["kwargs"] = kwargs
            return SimpleNamespace(content=blocks)

    return SimpleNamespace(messages=Messages()), calls


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(nodes.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(nodes, "_TRIAGE_CACHE", [])
    monkeypatch.setattr(nodes, "_embed_fn", lambda texts: [[1.0, 0.0] for _ in texts])


def _tool_use(**data):
    return SimpleNamespace(type="tool_use", input=data)


def test_triage_parses_tool_use_block(online, monkeypatch):
    client, calls = _fake_claude([_tool_use(
        error_code="0x0001", manufacturer="Huawei", severity="high", intent="fault",
        required_skills=["solar"], detected_language="en",
    )])
    monkeypatch.setattr(nodes, "_client", lambda: client)
    out = nodes.triage_node(_state())
    assert out["error_code"] == "0x0001" and out["manufacturer"] == "Huawei"
    assert out["severity"] == "high" and out["intent"] == "fault"
    assert out["iteration"] == 0
    # Forced tool call, so the response is schema-shaped rather than free text.
    assert calls["kwargs"]["tool_choice"] == {"type": "tool", "name": "record_triage_diagnosis"}


def test_triage_defaults_when_tool_use_block_missing(online, monkeypatch):
    client, _ = _fake_claude([SimpleNamespace(type="text", text="no tool call")])
    monkeypatch.setattr(nodes, "_client", lambda: client)
    out = nodes.triage_node(_state())
    assert out["severity"] == "medium" and out["intent"] == "general"
    assert out["required_skills"] == [] and out["detected_language"] == "de"
    assert out["error_code"] is None


def test_triage_fills_defaults_for_omitted_optional_fields(online, monkeypatch):
    client, _ = _fake_claude([_tool_use(severity="low", intent="billing", required_skills=[],
                                        detected_language="en")])
    monkeypatch.setattr(nodes, "_client", lambda: client)
    out = nodes.triage_node(_state())
    assert out["error_code"] is None and out["manufacturer"] is None


def test_second_similar_ticket_hits_cache_and_skips_claude(online, monkeypatch):
    client, calls = _fake_claude([_tool_use(severity="high", intent="fault",
                                            required_skills=["solar"], detected_language="en")])
    monkeypatch.setattr(nodes, "_client", lambda: client)
    nodes.triage_node(_state())
    out = nodes.triage_node(_state(ticket_id="T-2"))
    assert calls["n"] == 1
    assert out["severity"] == "high" and "cache hit" in out["messages"][0].content


def test_offline_mode_guesses_error_code_without_claude(monkeypatch):
    monkeypatch.setattr(nodes.settings, "anthropic_api_key", "")
    monkeypatch.setattr(nodes, "_client", lambda: pytest.fail("Claude must not be called offline"))
    out = nodes.triage_node(_state(raw_body="Wir haben Error 501 und es ist nicht gut"))
    assert out["error_code"] == "501" and out["intent"] == "fault"
    assert out["detected_language"] == "de"


def test_offline_mode_without_code_is_general_intent(monkeypatch):
    monkeypatch.setattr(nodes.settings, "anthropic_api_key", "")
    out = nodes.triage_node(_state(raw_subject="Invoice", raw_body="please send my invoice"))
    assert out["error_code"] is None and out["intent"] == "general"


# --- routing edges ---------------------------------------------------------

@pytest.mark.parametrize("intent,expected", [
    ("fault", "research"),
    ("billing", "decision"),
    ("reschedule", "decision"),
    ("general", "decision"),
    (None, "decision"),
])
def test_after_triage(intent, expected):
    assert nodes.after_triage({"intent": intent}) == expected


def test_after_inventory_in_stock_goes_to_routing():
    assert nodes.after_inventory({"part_in_stock": True, "iteration": 0}) == "routing"


def test_after_inventory_out_of_stock_retries_research():
    assert nodes.after_inventory({"part_in_stock": False, "iteration": 0}) == "research"


def test_after_inventory_stops_at_max_iterations():
    state = {"part_in_stock": False, "iteration": nodes.MAX_RESEARCH_ITERATIONS}
    assert nodes.after_inventory(state) == "routing"


def test_after_inventory_missing_iteration_treated_as_zero():
    assert nodes.after_inventory({"part_in_stock": False}) == "research"
