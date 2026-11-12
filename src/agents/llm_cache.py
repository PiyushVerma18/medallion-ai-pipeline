"""Cache structured LLM outputs so identical prompts replay exactly offline."""

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from src.config import GEMINI_API_KEY, GEMINI_MODEL

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger("medallion.pipeline")
_CALL_STATS = {
    "cache_hits": 0,
    "cache_misses": 0,
    "provider_calls": 0,
    "provider_input_chars": 0,
    "provider_output_chars": 0,
}


def reset_llm_call_stats() -> None:
    for key in _CALL_STATS:
        _CALL_STATS[key] = 0


def get_llm_call_stats() -> dict:
    return dict(_CALL_STATS)


def _cache_dir() -> Path:
    configured = os.getenv("LLM_CACHE_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parents[2] / "data" / "llm_cache"


def _normalize_messages(messages: Any) -> Any:
    if isinstance(messages, str):
        return messages
    if isinstance(messages, (list, tuple)):
        normalized = []
        for item in messages:
            if isinstance(item, tuple) and len(item) == 2:
                normalized.append({"role": str(item[0]), "content": item[1]})
            elif isinstance(item, dict):
                normalized.append(item)
            else:
                normalized.append(str(item))
        return normalized
    return str(messages)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".llm-cache-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def invoke_gemini_structured(
    output_model: Type[T],
    messages: Any,
    *,
    temperature: float = 0.0,
    model_name: str = GEMINI_MODEL,
) -> T:
    """Return cached structured output or call Gemini and persist its result.

    ``LLM_CACHE_MODE`` may be ``read_write`` (default), ``replay`` (cache only),
    or ``refresh`` (bypass cache and call Gemini). The cache key includes every
    input that can affect the result: model, temperature, prompt, and schema.
    """
    mode = os.getenv("LLM_CACHE_MODE", "read_write").strip().lower()
    if mode not in {"read_write", "replay", "refresh"}:
        raise ValueError("LLM_CACHE_MODE must be read_write, replay, or refresh")

    key_payload = {
        "provider": "google-gemini",
        "model": model_name,
        "temperature": temperature,
        "schema": output_model.model_json_schema(),
        "messages": _normalize_messages(messages),
    }
    canonical = json.dumps(key_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    cache_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    cache_path = _cache_dir() / f"{cache_key}.json"

    if mode != "refresh" and cache_path.is_file():
        with cache_path.open(encoding="utf-8") as cached_file:
            record = json.load(cached_file)
        if record.get("cache_key") != cache_key:
            raise ValueError(f"LLM cache integrity check failed for {cache_path.name}")
        _CALL_STATS["cache_hits"] += 1
        logger.info("llm_cache_hit model=%s schema=%s key=%s", model_name, output_model.__name__, cache_key)
        return output_model.model_validate(record["response"])

    _CALL_STATS["cache_misses"] += 1
    logger.info("llm_cache_miss model=%s schema=%s key=%s mode=%s", model_name, output_model.__name__, cache_key, mode)
    if mode == "replay":
        raise RuntimeError(
            f"No cached Gemini response for {output_model.__name__} input {cache_key}. "
            "Use a cache bundle containing this input or run once with a Gemini API key to record it."
        )
    if not GEMINI_API_KEY:
        raise RuntimeError(
            f"Gemini API key is required to create a cache entry for {output_model.__name__} "
            f"(input {cache_key}); no matching cached response was found."
        )

    from langchain_google_genai import ChatGoogleGenerativeAI

    os.environ["GOOGLE_API_KEY"] = GEMINI_API_KEY
    llm = ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=GEMINI_API_KEY,
        temperature=temperature,
    ).with_structured_output(output_model)
    _CALL_STATS["provider_calls"] += 1
    _CALL_STATS["provider_input_chars"] += len(canonical)
    response = llm.invoke(messages)
    response_data = response.model_dump(mode="json")
    response_json = json.dumps(response_data, ensure_ascii=False, sort_keys=True, default=str)
    _CALL_STATS["provider_output_chars"] += len(response_json)
    record = {
        "cache_key": cache_key,
        "provider": "google-gemini",
        "model": model_name,
        "temperature": temperature,
        "schema_name": output_model.__name__,
        "response": response_data,
    }
    _atomic_write(cache_path, json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    logger.info("llm_cache_written model=%s schema=%s key=%s", model_name, output_model.__name__, cache_key)
    return response
