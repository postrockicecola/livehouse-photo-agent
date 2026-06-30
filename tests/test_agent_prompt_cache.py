"""Tests for harness-layer prefix/prompt caching (services/agent/prompt_cache.py).

Covered behaviors:
- StablePrefix builds a deterministic, fingerprinted cacheable block; the fingerprint
  is stable across rebuilds and changes when the prefix drifts;
- canonical_text renders chat messages deterministically;
- PrefixCacheMeter (declared mode) splits each prompt into cached-prefix vs fresh-tail
  tokens, flags prefix_intact, and reports a hit rate;
- drift off the declared prefix is detected (prefix_intact False, lower cached count);
- rolling mode meters against the previous prompt;
- the metered_* wrappers are transparent drop-ins;
- multi-agent fan-out sharing one stable prefix yields a high prefix hit rate (the A↔B tie).
"""
from __future__ import annotations

from services.agent.prompt_cache import (
    PrefixCacheMeter,
    StablePrefix,
    canonical_text,
    common_prefix_len,
    default_token_estimate,
    metered_chat_fn,
    metered_complete_fn,
)


def _prefix() -> StablePrefix:
    return StablePrefix(
        system_prompt="You are a concert-photo culling agent.",
        tools=[{"name": "analyze", "args": {"idx": "int"}}],
    )


# --------------------------------------------------------------------- StablePrefix


def test_stable_prefix_fingerprint_is_stable_and_drift_sensitive():
    a = _prefix().fingerprint()
    b = _prefix().fingerprint()
    assert a == b  # same inputs → same fingerprint
    drifted = StablePrefix(system_prompt="You are a concert-photo culling agent!", tools=_prefix().tools)
    assert drifted.fingerprint() != a


def test_stable_prefix_messages_fold_tools_into_system():
    msgs = _prefix().messages()
    assert len(msgs) == 1 and msgs[0]["role"] == "system"
    assert "TOOLS:" in msgs[0]["content"]


def test_canonical_text_is_deterministic_for_messages():
    m1 = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
    m2 = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
    assert canonical_text(m1) == canonical_text(m2)
    assert canonical_text("raw") == "raw"


def test_common_prefix_len():
    assert common_prefix_len("abcXYZ", "abcDEF") == 3
    assert common_prefix_len("abc", "abc") == 3
    assert common_prefix_len("x", "y") == 0


# ---------------------------------------------------------------- meter: declared


def test_meter_declared_splits_prefix_and_tail():
    pre = _prefix()
    meter = PrefixCacheMeter(stable_prefix=pre)
    tail = "\nBUDGET: {...}\nCANDIDATES: [0,1,2]\nJSON action:"
    prompt = pre.text() + tail

    obs = meter.observe(prompt)
    assert obs.prefix_intact is True
    assert obs.cached_prefix_tokens == default_token_estimate(pre.text())
    assert obs.fresh_tokens == obs.total_tokens - obs.cached_prefix_tokens
    assert 0.0 < obs.hit_rate <= 1.0


def test_meter_declared_detects_drift():
    meter = PrefixCacheMeter(stable_prefix=_prefix())
    obs = meter.observe("COMPLETELY DIFFERENT HEAD\nthen some body text here")
    assert obs.prefix_intact is False
    assert obs.cached_prefix_tokens == 0


def test_meter_metrics_dict_rollup():
    pre = _prefix()
    meter = PrefixCacheMeter(stable_prefix=pre)
    for i in range(3):
        meter.observe(pre.text() + f"\nCANDIDATES: [{i}]")
    m = meter.metrics_dict()
    assert m["calls"] == 3
    assert m["prefix_intact_calls"] == 3
    assert m["cached_prefix_tokens"] > 0
    assert 0.0 < m["prefix_hit_rate"] <= 1.0
    assert m["stable_prefix_fingerprint"] == pre.fingerprint()


# ---------------------------------------------------------------- meter: rolling


def test_meter_rolling_meters_against_previous_prompt():
    meter = PrefixCacheMeter()  # no declared prefix → rolling mode
    head = "SHARED HEADER LINE THAT REPEATS\n"
    first = meter.observe(head + "first tail")
    assert first.cached_prefix_tokens == 0  # nothing to compare against yet
    second = meter.observe(head + "second different tail")
    assert second.cached_prefix_tokens > 0  # reused the shared head


# ---------------------------------------------------------------- metered wrappers


def test_metered_complete_fn_is_transparent_and_counts():
    meter = PrefixCacheMeter(stable_prefix=_prefix())
    calls: list[str] = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        return "ACTION"

    wrapped = metered_complete_fn(complete, meter)
    out = wrapped(_prefix().text() + "\ntail")
    assert out == "ACTION"
    assert len(calls) == 1
    assert meter.calls == 1


def test_metered_chat_fn_is_transparent_and_counts():
    meter = PrefixCacheMeter()
    wrapped = metered_chat_fn(lambda msgs: "hi", meter)
    out = wrapped([{"role": "user", "content": "yo"}])
    assert out == "hi"
    assert meter.calls == 1


# ---------------------------------------------------------------- A ↔ B tie


def test_multi_agent_fanout_shares_prefix_high_hit_rate():
    # Simulate N sub-agents (orchestrator fan-out): each issues a prompt that is the
    # SAME stable prefix + a small per-shard tail. The shared prefix dominates, so the
    # prefix-cache hit rate is high — this is why the fan-out is cheap.
    pre = _prefix()
    meter = PrefixCacheMeter(stable_prefix=pre)
    for shard in range(6):
        meter.observe(pre.text() + f"\nCANDIDATES: shard {shard}")
    m = meter.metrics_dict()
    assert m["prefix_intact_calls"] == 6
    assert m["prefix_hit_rate"] > 0.5  # prefix >> tiny tail
