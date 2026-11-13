"""Structured support-message triage with explicit LLM safety controls (W7 A17)."""

import json
import os
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ROOT = Path(__file__).resolve().parent
# Load local settings when present; real secrets stay in the ignored .env file.
load_dotenv(ROOT / ".env")

PROMPT_VERSION = "triage-v1"
PROMPT_PATH = ROOT / "prompts" / "triage-v1.md"
QUARANTINE_PATH = ROOT / "logs" / "quarantine.jsonl"
MODEL_TIMEOUT_SECONDS = 30.0
MAX_PROVIDER_ATTEMPTS = 3

app = FastAPI(title="Support Triage API", version="1.0")


class Category(str, Enum):
    billing = "billing"
    bug = "bug"
    feature = "feature"
    other = "other"


class Urgency(str, Enum):
    low = "low"
    normal = "normal"
    high = "high"


class TriageOutput(BaseModel):
    # `extra="forbid"` keeps unexpected model fields out of the API response.
    model_config = ConfigDict(extra="forbid")

    category: Category
    urgency: Urgency
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=240)


class ModelTimeout(Exception):
    pass


class ProviderUnavailable(Exception):
    pass


class ProviderRejected(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code


def enabled() -> bool:
    return os.getenv("LLM_ENABLED", "true").strip().lower() not in {"false", "0", "no"}


def stub_enabled() -> bool:
    return os.getenv("LLM_STUB", "0").strip() == "1"


def error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def emit_call_log(
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    duration_ms: int,
    repair_count: int,
    attempt: int,
    status: str,
) -> None:
    print(
        json.dumps(
            {
                "event": "llm_call",
                "timestamp": utc_now(),
                "prompt_version": PROMPT_VERSION,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_ms": duration_ms,
                "repair_count": repair_count,
                "attempt": attempt,
                "status": status,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )


def retry_after_seconds(exc: APIStatusError) -> float | None:
    value = exc.response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            return max(0.0, (when - datetime.now(when.tzinfo or timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def backoff_seconds(attempt: int, retry_after: float | None = None) -> float:
    # Grow the wait after repeated failures and add jitter so clients don't retry together.
    delay = retry_after if retry_after is not None else float(2 ** (attempt - 1))
    return delay + random.uniform(0.0, 0.25)


def model_client() -> OpenAI:
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not base_url or not api_key:
        raise RuntimeError("Set LLM_BASE_URL and LLM_API_KEY in .env to enable real model calls.")
    # Disable the SDK's hidden retries; this module owns the retry policy.
    return OpenAI(base_url=base_url, api_key=api_key, timeout=MODEL_TIMEOUT_SECONDS, max_retries=0)


def call_model(messages: list[dict[str, str]], repair_count: int) -> str:
    model = os.getenv("LLM_MODEL", "openrouter/free").strip()
    client = model_client()

    for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.0,
            )
        except APITimeoutError as exc:
            elapsed = round((time.monotonic() - started) * 1000)
            emit_call_log(
                model=model,
                input_tokens=None,
                output_tokens=None,
                duration_ms=elapsed,
                repair_count=repair_count,
                attempt=attempt,
                status="timeout",
            )
            if attempt < MAX_PROVIDER_ATTEMPTS:
                time.sleep(backoff_seconds(attempt))
                continue
            raise ModelTimeout from exc
        except RateLimitError as exc:
            elapsed = round((time.monotonic() - started) * 1000)
            emit_call_log(
                model=model,
                input_tokens=None,
                output_tokens=None,
                duration_ms=elapsed,
                repair_count=repair_count,
                attempt=attempt,
                status="rate_limited",
            )
            if attempt < MAX_PROVIDER_ATTEMPTS:
                time.sleep(backoff_seconds(attempt, retry_after_seconds(exc)))
                continue
            raise ProviderUnavailable("Provider rate limit persisted after retries") from exc
        except APIStatusError as exc:
            elapsed = round((time.monotonic() - started) * 1000)
            status = exc.status_code
            emit_call_log(
                model=model,
                input_tokens=None,
                output_tokens=None,
                duration_ms=elapsed,
                repair_count=repair_count,
                attempt=attempt,
                status=f"http_{status}",
            )
            if status >= 500 and attempt < MAX_PROVIDER_ATTEMPTS:
                time.sleep(backoff_seconds(attempt, retry_after_seconds(exc)))
                continue
            if status >= 500:
                raise ProviderUnavailable("Provider server error persisted after retries") from exc
            raise ProviderRejected(status) from exc
        except APIConnectionError as exc:
            elapsed = round((time.monotonic() - started) * 1000)
            emit_call_log(
                model=model,
                input_tokens=None,
                output_tokens=None,
                duration_ms=elapsed,
                repair_count=repair_count,
                attempt=attempt,
                status="connection_error",
            )
            raise ProviderUnavailable("Could not connect to the configured model provider") from exc

        elapsed = round((time.monotonic() - started) * 1000)
        usage = response.usage
        emit_call_log(
            model=model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            duration_ms=elapsed,
            repair_count=repair_count,
            attempt=attempt,
            status="ok",
        )
        return response.choices[0].message.content or ""

    raise ProviderUnavailable("Model provider did not return a response")


def parse_and_validate(raw: str) -> TriageOutput:
    # Models sometimes wrap JSON in prose or a code fence; extract one object, then validate it.
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in model response")
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError("Model response must be a JSON object")
    return TriageOutput.model_validate(value)


def append_quarantine(user_text: str, raw: str, reason: str) -> None:
    # Keep rejected output for debugging without ever returning it to the API caller.
    QUARANTINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": utc_now(),
        "prompt_version": PROMPT_VERSION,
        "input": user_text,
        "raw_output": raw,
        "error": reason,
    }
    with QUARANTINE_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def input_text(body: Any) -> tuple[str | None, str | None]:
    if not isinstance(body, dict):
        return None, "Request body must be a JSON object with field 'text'"
    if "text" not in body:
        return None, "Field 'text' is required"
    text = body["text"]
    if not isinstance(text, str):
        return None, "Field 'text' must be a string"
    text = text.strip()
    if not text:
        return None, "Field 'text' must not be empty"
    if len(text) > 2000:
        return None, "Field 'text' must be at most 2000 characters"
    return text, None


@app.get("/health", summary="Check that the API is running")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/triage", response_model=TriageOutput, summary="Classify one support message")
async def triage(request: Request):
    try:
        body = await request.json()
    except Exception:
        return error("Request body must be valid JSON; field 'text' is required", 400)

    text, issue = input_text(body)
    if issue:
        return error(issue, 400)

    if not enabled():
        # The kill switch keeps the endpoint useful without contacting the provider.
        return TriageOutput(
            category=Category.other,
            urgency=Urgency.normal,
            confidence=0.0,
            reason="The model is disabled; a human should review this message.",
        )

    if stub_enabled():
        return TriageOutput(
            category=Category.other,
            urgency=Urgency.normal,
            confidence=0.5,
            reason="Stub mode returns a fixed example response.",
        )

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    # JSON encoding keeps untrusted user content separate from the system instructions.
    user_message = json.dumps({"text": text}, ensure_ascii=False)
    initial_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    try:
        raw = call_model(initial_messages, repair_count=0)
    except ModelTimeout:
        return error("The model provider timed out", 504)
    except ProviderRejected as exc:
        if exc.status_code in {401, 403}:
            return error(f"The provider rejected authentication or access (HTTP {exc.status_code})", 502)
        return error(f"The provider rejected the request (HTTP {exc.status_code})", 502)
    except ProviderUnavailable as exc:
        return error(str(exc), 503)
    except RuntimeError as exc:
        return error(str(exc), 503)

    try:
        result = parse_and_validate(raw)
        return result
    except (ValueError, ValidationError) as first_issue:
        # Give the model one chance to correct its own invalid answer, then stop.
        repair_user_message = json.dumps(
            {
                "original_input": text,
                "previous_output": raw,
                "validation_error": str(first_issue),
                "instruction": "Your previous answer was rejected. Return only corrected JSON matching the schema.",
            },
            ensure_ascii=False,
        )
        repair_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": raw},
            {"role": "user", "content": repair_user_message},
        ]
        try:
            repaired_raw = call_model(repair_messages, repair_count=1)
            result = parse_and_validate(repaired_raw)
            return result
        except ModelTimeout:
            return error("The model provider timed out during the repair attempt", 504)
        except ProviderRejected as exc:
            return error(f"The provider rejected the repair request (HTTP {exc.status_code})", 502)
        except ProviderUnavailable as exc:
            return error(str(exc), 503)
        except RuntimeError as exc:
            return error(str(exc), 503)
        except (ValueError, ValidationError) as repair_issue:
            append_quarantine(text, repaired_raw, str(repair_issue))
            return error("The model could not produce a valid response after one repair attempt", 422)
