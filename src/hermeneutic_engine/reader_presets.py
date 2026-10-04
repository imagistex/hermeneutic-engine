"""Named readers. A preset pins the exact model so a lens stays comparable
over time; the model a harness actually reports is recorded on every activity."""

from __future__ import annotations


def preset(name: str):
    from .readers import ReaderSpec
    presets = {
        # Bulk coders, one per family. Opus 4.8 is pinned because Opus 5.5's
        # safeguards refuse some batches of this corpus (see lab/STATE.md).
        "opus": ReaderSpec("claude-cli", "claude-opus-4-8", "anthropic", {}),
        "sol": ReaderSpec("codex-cli", "gpt-6.1-sol", "openai", {"effort": "medium"}),
        # The standard tier. The "-contributor" tier is cheaper in exchange for
        # permission to train on prompts and completions.
        "muse": ReaderSpec("openai-http", "muse-spark-1.3", "meta",
                           {"base_url": "https://api.meta.ai/v1", "api_key_env": "MODEL_API_KEY"}),
        # A separate lane: the newer Anthropic model, where it will read.
        "opus55": ReaderSpec("claude-cli", "claude-opus-5-5", "anthropic", {}),
        # Senior readers, for consolidation and adjudication, not bulk coding.
        "fable": ReaderSpec("claude-cli", "claude-fable-5-1", "anthropic", {}),
        "astra": ReaderSpec("codex-cli", "gpt-6-astra", "openai", {"effort": "high"}),
    }
    if name not in presets:
        raise ValueError(f"unknown reader: {name} (known: {', '.join(sorted(presets))})")
    return presets[name]
