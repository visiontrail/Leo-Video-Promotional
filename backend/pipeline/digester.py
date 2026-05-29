import json
import logging
import httpx
from backend import config
from backend.pipeline.extractors.base import ExtractedContent

logger = logging.getLogger(__name__)

MAX_CHUNK_WORDS = 6000


async def _chat(system_prompt: str, user_content: str, ai_endpoint: str | None = None, ai_model: str | None = None) -> str:
    endpoint = ai_endpoint or config.AI_ENDPOINT
    model = ai_model or config.AI_MODEL

    async with httpx.AsyncClient(timeout=config.AI_TIMEOUT) as client:
        for attempt in range(config.AI_MAX_RETRIES + 1):
            try:
                resp = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {config.AI_API_KEY}", "Content-Type": "application/json"},
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


async def summarize(content: ExtractedContent, ai_endpoint: str | None = None, ai_model: str | None = None) -> dict:
    logger.info(f"Summarizing content: {content.title} ({len(content.text.split())} words)")

    system_prompt = (config.PROMPTS_DIR / "summarize.txt").read_text()

    chunks = _chunk_text(content.text)

    if len(chunks) == 1:
        result = await _chat(system_prompt, content.text, ai_endpoint, ai_model)
    else:
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            logger.info(f"  Summarizing chunk {i + 1}/{len(chunks)}")
            partial = await _chat(system_prompt, chunk, ai_endpoint, ai_model)
            chunk_summaries.append(partial)

        merge_prompt = (
            "You are a content analyst. Merge these partial summaries into a single coherent summary. "
            "Output the same JSON format as the individual summaries, combining the best talking points "
            "and key quotes from all parts. Keep only the top 5-8 talking points and 3-5 quotes."
        )
        combined = "\n\n---\n\n".join(chunk_summaries)
        result = await _chat(merge_prompt, combined, ai_endpoint, ai_model)

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
) -> str:
    word_count = target_duration_minutes * 150
    logger.info(f"Generating podcast script (~{word_count} words, {target_duration_minutes} min)")

    system_prompt = (config.PROMPTS_DIR / "scriptwrite.txt").read_text()
    system_prompt = system_prompt.replace("{word_count}", str(word_count))
    system_prompt = system_prompt.replace("{duration_minutes}", str(target_duration_minutes))

    user_content = json.dumps(summary, indent=2, ensure_ascii=False)
    script = await _chat(system_prompt, user_content, ai_endpoint, ai_model)

    clean = script.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    lines = [l for l in clean.splitlines() if l.strip().startswith("Speaker")]
    if not lines:
        raise RuntimeError("Generated script contains no valid Speaker lines")

    return "\n".join(lines)
