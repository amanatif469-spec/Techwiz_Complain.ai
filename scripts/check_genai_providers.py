"""Probe each configured GenAI provider and report which ones can serve Pipeline 1.

Run with:  python scripts/check_genai_providers.py
Prints provider names and errors only -- never API keys.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import CHAT_PROVIDERS, get_settings  # noqa: E402
from genai_pipeline.client import GenAIError, _dispatch, _model_name, provider_chain  # noqa: E402

SYSTEM = "You are a JSON API. Reply with JSON only."
USER = 'Return exactly this object: {"ok": true}'


def main() -> int:
    settings = get_settings()
    chain = provider_chain(settings)
    print(f"Configured keys : {[p for p in CHAT_PROVIDERS if settings.provider_has_key(p)]}")
    print(f"Fallback chain  : {chain or 'EMPTY — no provider key configured'}\n")

    healthy = []
    for provider in chain:
        label = f"{provider} ({_model_name(provider, settings)})"
        try:
            _dispatch(provider, SYSTEM, USER, settings, settings.genai_timeout_seconds)
        except GenAIError as exc:
            print(f"  FAIL  {label}\n        {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  {label}\n        {type(exc).__name__}: {exc}")
        else:
            healthy.append(provider)
            print(f"  OK    {label}")

    print(f"\nUsable providers: {healthy or 'NONE — complaints will fall back to Python ground truth only'}")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
