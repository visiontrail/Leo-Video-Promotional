import json
import logging
import time
import httpx
from collections.abc import Callable
from backend import config
from backend.pipeline.extractors.base import ExtractedContent

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

MAX_CHUNK_WORDS = 6000


def _dlog(log: LogCallback | None, message: str):
    """Info line to the task log when present (start.sh + pipeline.log + UI),
    else the module logger (start.sh only). Prefer the callback to avoid
    double-logging to start.sh."""
    if log is not None:
        log(message)
    else:
        logger.info(message)


def _dwarn(log: LogCallback | None, message: str):
    """Warning that always reaches start.sh at WARNING level, and the in-app
    LogPanel too when a task callback is present."""
    logger.warning(message)
    if log is not None:
        log(message)


async def _resolve_provider(
    provider_id: int | None,
    ai_endpoint: str | None,
    ai_model: str | None,
) -> tuple[str, str, str]:
    """Resolve (endpoint, model, api_key) for a digestion call.

    Explicit ai_endpoint/ai_model overrides take precedence; otherwise the
    provider config is loaded from the database (by id, or the default
    provider when id is None); falling back to the .env defaults."""
    from backend import database

    endpoint = ai_endpoint
    model = ai_model
    api_key = config.AI_API_KEY

    if endpoint is None or model is None:
        row = await database.get_provider_raw(provider_id)
        if row is not None:
            endpoint = endpoint or row["endpoint"]
            model = model or row["model"]
            api_key = row["api_key"] or ""

    return (endpoint or config.AI_ENDPOINT, model or config.AI_MODEL, api_key)


async def _chat(
    system_prompt: str,
    user_content: str,
    endpoint: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    log: LogCallback | None = None,
    label: str = "AI call",
) -> str:
    endpoint = endpoint or config.AI_ENDPOINT
    model = model or config.AI_MODEL
    api_key = api_key if api_key is not None else config.AI_API_KEY

    _dlog(log, f"{label}: POST {endpoint} (model={model}, ~{len(user_content.split())} words in)")

    async with httpx.AsyncClient(timeout=config.AI_TIMEOUT) as client:
        for attempt in range(config.AI_MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                resp = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 4096,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                elapsed_ms = (time.perf_counter() - start) * 1000
                usage = data.get("usage") or {}
                usage_str = (
                    f", tokens={usage.get('prompt_tokens', '?')}+{usage.get('completion_tokens', '?')}"
                    f"={usage.get('total_tokens', '?')}"
                    if usage
                    else ""
                )
                _dlog(log, f"{label}: {model} responded in {elapsed_ms:.0f}ms ({len(content)} chars{usage_str})")
                return content
            except httpx.HTTPStatusError as e:
                # raise_for_status()'s message omits the response body, which is
                # where the API's real error detail lives (bad path, unknown
                # model, auth, quota...). Surface status + URL + body.
                body = e.response.text[:1000].strip() if e.response is not None else ""
                detail = (
                    f"HTTP {e.response.status_code} {e.response.reason_phrase} "
                    f"from {e.request.method} {e.request.url} (model={model}); "
                    f"response body: {body or '<empty>'}"
                )
                _dwarn(log, f"{label} attempt {attempt + 1} failed: {detail}")
                if attempt == config.AI_MAX_RETRIES:
                    raise RuntimeError(f"AI request failed: {detail}") from e
            except httpx.RequestError as e:
                # Connection refused, DNS failure, timeout — no response body.
                detail = f"{e.__class__.__name__} connecting to {endpoint} (model={model}): {e}"
                _dwarn(log, f"{label} attempt {attempt + 1} failed: {detail}")
                if attempt == config.AI_MAX_RETRIES:
                    raise RuntimeError(f"AI request failed: {detail}") from e
            except Exception as e:
                _dwarn(log, f"{label} attempt {attempt + 1} failed: {e.__class__.__name__}: {e}")
                if attempt == config.AI_MAX_RETRIES:
                    raise


async def test_connection(endpoint: str, model: str, api_key: str) -> int:
    """Send a minimal chat request to verify the provider is reachable and the
    model responds in the expected OpenAI-compatible shape. Returns the round-trip
    latency in milliseconds, or raises on any failure."""
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=config.AI_TIMEOUT) as client:
        resp = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        # Validate the response has the expected OpenAI-compatible shape.
        data["choices"][0]["message"]
    return int((time.perf_counter() - start) * 1000)


def _chunk_text(text: str, max_words: int = MAX_CHUNK_WORDS) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]
    chunks = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i:i + max_words]))
    return chunks


def _summary_from_curated_highlights(content: ExtractedContent) -> dict:
    highlights = content.metadata.get("curated_highlights") or []
    talking_points = []
    key_quotes = []

    for item in highlights:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("highlightText") or item.get("highlight_text") or "").strip()
        if not quote:
            continue
        chapter = str(item.get("chapterTitle") or item.get("chapter_title") or "").strip()
        reason = str(item.get("selectionReason") or item.get("selection_reason") or "").strip()
        note = str(item.get("noteText") or item.get("note_text") or "").strip()
        post_title = str(item.get("postTitle") or item.get("post_title") or "").strip()
        post_description = str(item.get("postDescription") or item.get("post_description") or "").strip()

        talking_points.append(
            {
                "topic": post_title or chapter or "Curated highlight",
                "detail": post_description or note or reason or quote,
                "why_it_matters": reason,
                "chapter": chapter,
            }
        )
        key_quotes.append(quote)

    return {
        "title": content.title,
        "thesis": "This book discussion is based on Isla-Reader's pre-selected highlights, focusing on the most quotable and discussion-worthy ideas.",
        "talking_points": talking_points,
        "key_quotes": key_quotes,
        "discussion_angles": [
            "Open by explaining why these highlights stood out.",
            "Connect quotes across chapters into a coherent argument.",
            "Let the two hosts react naturally to the strongest lines and reader notes.",
        ],
        "source_format": "curated_highlights",
    }


async def summarize(
    content: ExtractedContent,
    ai_endpoint: str | None = None,
    ai_model: str | None = None,
    provider_id: int | None = None,
    log: LogCallback | None = None,
) -> dict:
    _dlog(log, f"Summarizing content: {content.title} ({len(content.text.split())} words)")

    if content.metadata.get("processing_mode") == "curated_highlights":
        _dlog(log, "Using Isla-Reader curated highlights as pre-selected talking points")
        return _summary_from_curated_highlights(content)

    endpoint, model, api_key = await _resolve_provider(provider_id, ai_endpoint, ai_model)
    system_prompt = (config.PROMPTS_DIR / "summarize.txt").read_text()

    chunks = _chunk_text(content.text)
    _dlog(log, f"Summarizing in {len(chunks)} chunk(s)")

    if len(chunks) == 1:
        result = await _chat(system_prompt, content.text, endpoint, model, api_key, log, "Summarize")
    else:
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            partial = await _chat(
                system_prompt, chunk, endpoint, model, api_key, log, f"Summarize chunk {i + 1}/{len(chunks)}"
            )
            chunk_summaries.append(partial)

        merge_prompt = (
            "You are a content analyst. Merge these partial summaries into a single coherent summary. "
            "Output the same JSON format as the individual summaries, combining the best talking points "
            "and key quotes from all parts. Keep only the top 5-8 talking points and 3-5 quotes."
        )
        combined = "\n\n---\n\n".join(chunk_summaries)
        result = await _chat(merge_prompt, combined, endpoint, model, api_key, log, "Summarize merge")

    try:
        clean = result.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
        parsed = json.loads(clean)
        _dlog(log, f"Summary parsed: {len(parsed.get('talking_points', []))} talking points, {len(parsed.get('key_quotes', []))} quotes")
        return parsed
    except json.JSONDecodeError:
        _dwarn(log, "Failed to parse summary JSON, returning raw text")
        return {"title": content.title, "thesis": result, "talking_points": [], "key_quotes": [], "discussion_angles": []}


SCRIPT_PROMPT_FILES = {
    "monologue": "scriptwrite_monologue.txt",
    "dialogue": "scriptwrite_dialogue.txt",
}


async def generate_script(
    summary: dict,
    target_duration_minutes: int = 10,
    script_format: str = "monologue",
    ai_endpoint: str | None = None,
    ai_model: str | None = None,
    provider_id: int | None = None,
    log: LogCallback | None = None,
) -> str:
    word_count = target_duration_minutes * 150
    prompt_file = SCRIPT_PROMPT_FILES.get(script_format, SCRIPT_PROMPT_FILES["monologue"])
    _dlog(
        log,
        f"Generating {script_format} script (~{word_count} words, {target_duration_minutes} min) "
        f"using {prompt_file}",
    )

    endpoint, model, api_key = await _resolve_provider(provider_id, ai_endpoint, ai_model)
    system_prompt = (config.PROMPTS_DIR / prompt_file).read_text()
    system_prompt = system_prompt.replace("{word_count}", str(word_count))
    system_prompt = system_prompt.replace("{duration_minutes}", str(target_duration_minutes))

    user_content = json.dumps(summary, indent=2, ensure_ascii=False)
    script = await _chat(system_prompt, user_content, endpoint, model, api_key, log, "Scriptwrite")

    clean = script.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    lines = [l for l in clean.splitlines() if l.strip().startswith("Speaker")]
    if not lines and script_format == "monologue":
        # Monologue prompts occasionally return plain prose without the
        # "Speaker 1:" prefix; wrap each beat so captions and TTS still work.
        lines = [f"Speaker 1: {l.strip()}" for l in clean.splitlines() if l.strip()]
        if lines:
            _dlog(log, f"Monologue had no speaker labels; wrapped {len(lines)} beats as Speaker 1")
    if not lines:
        raise RuntimeError("Generated script contains no valid Speaker lines")

    _dlog(log, f"Script generated: {len(lines)} speaker turns, {len(' '.join(lines).split())} words")
    return "\n".join(lines)
