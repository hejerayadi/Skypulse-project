"""
The Groq client behind every interpretation on the dashboard.

Server side on purpose. The key lives in backend/.env and never reaches the
browser, and the prompts are built here from data the backend already holds —
so a page asking for an interpretation sends a sensor id and a date range, not
a payload it could have tampered with.

Guarded the same way the RF detector is: a missing key or a missing package
switches the feature off and reports why, rather than turning every chart into
an error.
"""

from __future__ import annotations

import os
import threading

from dotenv import load_dotenv

load_dotenv()

# The key is read from `groq_model` first because that is what backend/.env
# already calls it. GROQ_API_KEY is the Groq SDK's own convention and is
# accepted as an alias — despite the name, the value is an API key, not a model.
API_KEY = os.getenv("groq_model") or os.getenv("GROQ_API_KEY")

MODEL = os.getenv("GROQ_MODEL_NAME", "openai/gpt-oss-120b")

# gpt-oss spends part of its completion budget on hidden reasoning tokens, so
# this is not "how long the answer may be" — it is reasoning plus answer. Too
# small and the answer comes back empty because the budget ran out mid-thought.
MAX_COMPLETION_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "1024"))

# "low" by default: every prompt here asks for two or three sentences, and
# deeper reasoning mostly buys tokens rather than insight at that length.
REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "low")

TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "1"))

# Short, because these calls sit in front of a chart. A slow answer is worse
# than no answer: the panel says the note is unavailable and moves on.
TIMEOUT_SECONDS = float(os.getenv("GROQ_TIMEOUT", "20"))

_lock = threading.Lock()
_client = None
_error: str | None = None
_loaded = False


class AIUnavailable(RuntimeError):
    """No key, no package, or the call failed. Never fatal to the caller."""


def load() -> bool:
    """Build the client once. Never raises."""

    global _client, _error, _loaded

    with _lock:
        if _loaded:
            return _client is not None
        _loaded = True

        if not API_KEY:
            _error = (
                "No Groq API key. Set `groq_model` (or GROQ_API_KEY) in backend/.env."
            )
            print("AI interpretations disabled -", _error)
            return False

        try:
            from groq import Groq
        except ImportError:
            _error = "The groq package is not installed. Run: pip install -r backend/requirements.txt"
            print("AI interpretations disabled -", _error)
            return False

        try:
            _client = Groq(api_key=API_KEY, timeout=TIMEOUT_SECONDS)
        except Exception as e:
            _error = f"Could not create the Groq client: {e}"
            print("AI interpretations disabled -", _error)
            return False

        print(f"AI interpretations enabled: {MODEL} (reasoning effort {REASONING_EFFORT})")
        return True


def available() -> bool:
    load()
    return _client is not None


def error() -> str | None:
    load()
    return _error


def status() -> dict:
    return {
        "available": available(),
        "error": error(),
        "model": MODEL if available() else None,
        "reasoning_effort": REASONING_EFFORT,
    }


def complete(system: str, user: str) -> str:
    """One short completion from a single system/user pair."""

    return complete_messages(
        [{"role": "system", "content": system}, {"role": "user", "content": user}]
    )


def complete_messages(messages: list[dict], json_mode: bool = False) -> str:
    """
    One completion from a full message list. Blocking — callers on the event
    loop must run it in a threadpool.

    `json_mode` constrains the reply to a single JSON object, which is what the
    copilot uses to return an answer and a navigation target together instead
    of having the backend parse intent out of prose.
    """

    if not available():
        raise AIUnavailable(_error or "AI is not configured")

    extra = {"response_format": {"type": "json_object"}} if json_mode else {}

    try:
        response = _client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            top_p=1,
            reasoning_effort=REASONING_EFFORT,
            # Not streamed: these are a few sentences that arrive in under a
            # second, and a stream would buy nothing but plumbing.
            stream=False,
            stop=None,
            **extra,
        )
    except Exception as e:
        raise AIUnavailable(f"Groq request failed: {e}") from e

    content = (response.choices[0].message.content or "").strip()

    if not content:
        # Happens when the whole token budget went to reasoning. Worth naming,
        # because the fix is GROQ_MAX_TOKENS rather than anything in the prompt.
        raise AIUnavailable(
            "The model returned no text — its token budget went entirely to "
            f"reasoning. Raise GROQ_MAX_TOKENS (currently {MAX_COMPLETION_TOKENS})."
        )

    return content
