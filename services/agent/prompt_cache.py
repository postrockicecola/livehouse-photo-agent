"""Harness-layer prefix / prompt caching: keep a stable prefix and measure its reuse.

This is the *context-engineering* counterpart to the semantic response cache
(:mod:`services.cache.semantic_cache`). Together they form a layered cache:

    L0  exact-prompt repeat        → skip the call entirely
    L1  prefix / KV reuse          → THIS module: a byte-stable leading prefix lets the
                                      serving engine (vLLM automatic prefix caching /
                                      OpenAI prompt caching) reuse attention KV for the
                                      shared tokens, so only the per-call tail is prefilled
    L2  semantic near-duplicate    → SemanticCache: reuse a result for a *similar* prompt

The honest boundary (stated, not hidden): **the KV cache itself lives in the inference
engine, not here.** A harness cannot reuse attention state from Python. What a harness
*can* do — and what this module does — is two things the engine can't do for you:

1. **Guarantee a stable prefix.** :class:`StablePrefix` builds the system prompt + tool
   specs into one canonical, byte-identical block with a fingerprint, so the cacheable
   region never silently drifts between turns / sub-agents (a CI test can assert the
   fingerprint). Prefix caches only hit on *identical* leading tokens, so keeping the
   prefix stable is the entire job.
2. **Measure the reuse.** :class:`PrefixCacheMeter` reports, per call, how many leading
   tokens are the shared prefix vs the fresh tail — i.e. the *upper bound* on what an
   upstream prefix cache can reuse, and a hit-rate the same way the other caches report
   theirs. In a multi-agent fan-out (see :mod:`services.agent.orchestrator`) every
   sub-agent shares the same prefix, so this is the number that explains why the
   fan-out is cheap.

The tokenizer is injected (``TokenizeFn = (text) -> int``) so this module has no model
dependency; the default ~4-chars/token estimate matches
:func:`services.agent.conversation.approx_tokens`, and production can pass a real
tokenizer for exact counts.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

# (text) -> token count. Default below; inject a real tokenizer for exact numbers.
TokenizeFn = Callable[[str], int]


def default_token_estimate(text: str) -> int:
    """~4 chars/token, dependency-free. Matches conversation.approx_tokens."""
    return max(0, len(text) // 4)


Messages = Sequence[dict[str, Any]]


def canonical_text(prompt: "str | Messages") -> str:
    """Serialize a prompt (raw string or chat messages) to one canonical string.

    Messages are rendered deterministically as ``role\\ncontent`` blocks so the same
    logical prompt always yields identical bytes — the precondition for any prefix cache
    (or our fingerprint) to be meaningful.
    """
    if isinstance(prompt, str):
        return prompt
    parts: list[str] = []
    for m in prompt:
        role = str(m.get("role", ""))
        content = m.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, sort_keys=True)
        name = m.get("name")
        head = f"{role}:{name}" if name else role
        parts.append(f"<{head}>\n{content}")
    return "\n".join(parts)


def common_prefix_len(a: str, b: str) -> int:
    """Length (chars) of the longest common leading run of two strings."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


@dataclass
class StablePrefix:
    """The byte-stable, cacheable head of every prompt: system prompt + tool specs.

    Put everything that does NOT change across turns / sub-agents here, and keep the
    per-call dynamic data (candidates, the user question, budget) in the *tail*. The
    ``fingerprint`` lets a test assert the prefix hasn't drifted — drift silently
    destroys prefix-cache hit rate, so it is worth pinning.
    """

    system_prompt: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    extra: str = ""  # any other static instructions kept in the cacheable region

    def messages(self) -> list[dict[str, Any]]:
        """The prefix as chat messages (system block; tools folded into system content)."""
        content = self.system_prompt
        if self.tools:
            content += "\n\nTOOLS: " + json.dumps(self.tools, ensure_ascii=False, sort_keys=True)
        if self.extra:
            content += "\n" + self.extra
        return [{"role": "system", "content": content}]

    def text(self) -> str:
        return canonical_text(self.messages())

    def fingerprint(self) -> str:
        """Stable sha256 of the canonical prefix bytes (truncated for readability)."""
        return hashlib.sha256(self.text().encode("utf-8")).hexdigest()[:16]

    def token_estimate(self, tokenize: TokenizeFn = default_token_estimate) -> int:
        return tokenize(self.text())


@dataclass
class PrefixObservation:
    """Per-call breakdown: how much of the prompt was the reusable prefix vs fresh tail."""

    total_tokens: int
    cached_prefix_tokens: int
    fresh_tokens: int
    prefix_intact: bool  # did the declared stable prefix appear, whole, at the head?

    @property
    def hit_rate(self) -> float:
        return (self.cached_prefix_tokens / self.total_tokens) if self.total_tokens else 0.0


class PrefixCacheMeter:
    """Measures prefix reuse across successive LLM calls (an accounting tool, not a KV cache).

    Two modes:

    - **Declared** (``stable_prefix`` given): each call is measured against the known
      cacheable region — the intended, deterministic number, and ``prefix_intact``
      flags any call whose head drifted off the declared prefix.
    - **Rolling** (no ``stable_prefix``): each call is measured against the *previous*
      call's prompt, modelling the engine's real behaviour (it reuses KV for whatever
      leading tokens happen to match the last request).
    """

    def __init__(
        self,
        *,
        stable_prefix: Optional[StablePrefix] = None,
        tokenize: TokenizeFn = default_token_estimate,
    ) -> None:
        self._stable = stable_prefix
        self._stable_text = stable_prefix.text() if stable_prefix else None
        self._tokenize = tokenize
        self._prev_text: Optional[str] = None
        self.calls = 0
        self.total_tokens = 0
        self.cached_prefix_tokens = 0
        self.fresh_tokens = 0
        self.prefix_intact_calls = 0

    def observe(self, prompt: "str | Messages") -> PrefixObservation:
        """Record one outbound prompt and return its prefix/tail breakdown."""
        text = canonical_text(prompt)
        total = self._tokenize(text)

        baseline = self._stable_text if self._stable_text is not None else self._prev_text
        if baseline:
            shared_chars = common_prefix_len(text, baseline)
            # In declared mode, only count up to the full declared prefix as "cached":
            # anything beyond it is genuinely fresh tail this call.
            if self._stable_text is not None:
                shared_chars = min(shared_chars, len(self._stable_text))
            cached = self._tokenize(text[:shared_chars])
            intact = self._stable_text is not None and shared_chars == len(self._stable_text)
        else:
            cached = 0
            intact = False

        cached = min(cached, total)
        fresh = total - cached

        self.calls += 1
        self.total_tokens += total
        self.cached_prefix_tokens += cached
        self.fresh_tokens += fresh
        if intact:
            self.prefix_intact_calls += 1
        self._prev_text = text

        return PrefixObservation(
            total_tokens=total,
            cached_prefix_tokens=cached,
            fresh_tokens=fresh,
            prefix_intact=intact,
        )

    def metrics_dict(self) -> dict[str, Any]:
        """Roll-up surface, mirroring the hit_rate/saved style of the other caches."""
        return {
            "calls": self.calls,
            "prompt_tokens_total": self.total_tokens,
            "cached_prefix_tokens": self.cached_prefix_tokens,
            "fresh_tokens": self.fresh_tokens,
            # Fraction of all prompted tokens that fall in a reusable prefix — the
            # ceiling on what an upstream prefix/KV cache can avoid re-prefilling.
            "prefix_hit_rate": (self.cached_prefix_tokens / self.total_tokens) if self.total_tokens else 0.0,
            "prefix_intact_calls": self.prefix_intact_calls,
            "stable_prefix_fingerprint": self._stable.fingerprint() if self._stable else None,
        }


def metered_complete_fn(complete_fn: Callable[[str], str], meter: PrefixCacheMeter) -> Callable[[str], str]:
    """Wrap a planner ``CompleteFn`` (str prompt -> str) so every call is metered.

    Drop-in: ``LLMPlanner(metered_complete_fn(complete, meter))`` — no planner change.
    """

    def wrapped(prompt: str) -> str:
        meter.observe(prompt)
        return complete_fn(prompt)

    return wrapped


def metered_chat_fn(
    chat_fn: Callable[[list[dict[str, str]]], str], meter: PrefixCacheMeter
) -> Callable[[list[dict[str, str]]], str]:
    """Wrap a conversational ``ChatFn`` (messages -> str) so every call is metered.

    Drop-in: ``ConversationalAgent(metered_chat_fn(chat, meter), ...)`` — no agent change.
    """

    def wrapped(messages: list[dict[str, str]]) -> str:
        meter.observe(messages)
        return chat_fn(messages)

    return wrapped
