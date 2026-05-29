import json
import logging
import httpx
from backend import config
from backend.pipeline.extractors.base import ExtractedContent

logger = logging.getLogger(__name__)

MAX_CHUNK_WORDS = 6000


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
) -> str:
    endpoint = endpoint or config.AI_ENDPOINT
    model = model or config.AI_MODEL
    api_key = api_key if api_key is not None else config.AI_API_KEY

    async with httpx.AsyncClient(timeout=config.AI_TIMEOUT) as client:
        for attempt in range(config.AI_MAX_RETRIES + 1):
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
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                logger.warning(f"AI call attempt {attempt + 1} failed: {e}")
                if attempt == config.AI_MAX_RETRIES:
                    raise


def _chunk_text(text: str, max_words: int = MAX_CHUNK_WORDS) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]
    chunks = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i:i + max_words]))
    return chunks


async def summarize(
    content: ExtractedContent,
    ai_endpoint: str | None = None,
    ai_model: str | None = None,
    provider_id: int | None = None,
) -> dict:
    logger.info(f"Summarizing content: {content.title} ({len(content.text.split())} words)")

    endpoint, model, api_key = await _resolve_provider(provider_id, ai_endpoint, ai_model)
    system_prompt = (config.PROMPTS_DIR / "summarize.txt").read_text()

    chunks = _chunk_text(content.text)

    if len(chunks) == 1:
        result = await _chat(system_prompt, content.text, endpoint, model, api_key)
    else:
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            logger.info(f"  Summarizing chunk {i + 1}/{len(chunks)}")
            partial = await _chat(system_prompt, chunk, endpoint, model, api_key)
            chunk_summaries.append(partial)

        merge_prompt = (
            "You are a content analyst. Merge these partial summaries into a single coherent summary. "
            "Output the same JSON format as the individual summaries, combining the best talking points "
            "and key quotes from all parts. Keep only the top 5-8 talking points and 3-5 quotes."
        )
        combined = "\n\n---\n\n".join(chunk_summaries)
        result = await _chat(merge_prompt, combined, endpoint, model, api_key)

    try:
        clean = result.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(clean)
    except json.JSONDecodeError:
        logger.warning("Failed to parse summary JSON, returning raw text")
        return {"title": content.title, "thesis": result, "talking_points": [], "key_quotes": [], "discussion_angles": []}


async def generate_script(
    summary: dict,
    target_duration_minutes: int = 10,
    ai_endpoint: str | None = None,
    ai_model: str | None = None,
    provider_id: int | None = None,
) -> str:
    word_count = target_duration_minutes * 150
    logger.info(f"Generating podcast script (~{word_count} words, {target_duration_minutes} min)")

    endpoint, model, api_key = await _resolve_provider(provider_id, ai_endpoint, ai_model)
    system_prompt = (config.PROMPTS_DIR / "scriptwrite.txt").read_text()
    system_prompt = system_prompt.replace("{word_count}", str(word_count))
    system_prompt = system_prompt.replace("{duration_minutes}", str(target_duration_minutes))

    user_content = json.dumps(summary, indent=2, ensure_ascii=False)
    script = await _chat(system_prompt, user_content, endpoint, model, api_key)

    clean = script.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    lines = [l for l in clean.splitlines() if l.strip().startswith("Speaker")]
    if not lines:
        raise RuntimeError("Generated script contains no valid Speaker lines")

    return "\n".join(lines)
