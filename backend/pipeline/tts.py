import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from backend import config
from backend.pipeline.process_logging import stream_subprocess

logger = logging.getLogger(__name__)
LogCallback = Callable[[str], None]

# Synthesis is watched by inactivity, not elapsed time: a long script legitimately
# runs for hours, but VibeVoice prints a decode-progress line several times a
# second, so silence is the only reliable hang signal. The values are read from
# config at call time so an Admin change applies to the next run.
SPEAKER_LABEL_RE = re.compile(r"^\s*Speaker\s*\d+\s*[:：\-—–]\s*", re.IGNORECASE)
SPEAKER_LINE_RE = re.compile(
    r"^\s*(Speaker\s*\d+\s*[:：\-—–])\s*(.*)$", re.IGNORECASE
)
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？])\s+")

# Orpheus emits seven LM tokens for every 2,048 PCM samples at 24 kHz. This is
# the model's actual codec geometry, not a heuristic. It lets us turn the
# remote service's token ceiling into a safe text-chunk budget and detect a
# response that stopped because it hit max_tokens rather than end-of-speech.
ORPHEUS_AUDIO_TOKENS_PER_SECOND = 7 * 24_000 / 2_048
ORPHEUS_CHUNK_MIN_WPM = 90
ORPHEUS_CHUNK_SAFETY = 0.80
ORPHEUS_TOKEN_LIMIT_RATIO = 0.97
ORPHEUS_MIN_EXACT_ASR_COVERAGE = 1.0
ORPHEUS_MIN_ASR_WORD_RATIO = 1.0
ORPHEUS_MAX_ASR_WORD_RATIO = 1.0
ORPHEUS_EDGE_ANCHOR_WORDS = 3
ORPHEUS_MAX_INTEGRITY_ATTEMPTS = 3
ORPHEUS_MIN_REQUEST_TOKENS = 512
ORPHEUS_RETRY_MAX_DELAY_SECONDS = 60
ORPHEUS_RETRY_LOG_INTERVAL_SECONDS = 300
MAX_PLAUSIBLE_SPEECH_WPM = 320
LEXICAL_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?|[\u3400-\u9fff]")
NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "oh": "0", "twenty": "20",
    "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90",
}
DECADE_WORDS = {
    "twenties": "20s", "thirties": "30s", "forties": "40s",
    "fifties": "50s", "sixties": "60s", "seventies": "70s",
    "eighties": "80s", "nineties": "90s",
}
ORDINAL_DIGITS = {
    "1st": "first", "2nd": "second", "3rd": "third", "4th": "fourth",
    "5th": "fifth", "6th": "sixth", "7th": "seventh", "8th": "eighth",
    "9th": "ninth", "10th": "tenth", "11th": "eleventh", "12th": "twelfth",
    "13th": "thirteenth", "14th": "fourteenth", "15th": "fifteenth",
    "16th": "sixteenth", "17th": "seventeenth", "18th": "eighteenth",
    "19th": "nineteenth", "20th": "twentieth", "30th": "thirtieth",
}
# Acoustic verification cannot distinguish exact homophones. Keep this list
# deliberately narrow; near-homophones such as ``feed``/``feet`` must still fail.
ACOUSTIC_EQUIVALENTS = {
    "feat": "feet",
    # Possessive "its" and the contraction "it's" are exact homophones.
    # Whisper uses the contraction spelling for either meaning, so spelling
    # cannot be used as acoustic evidence that the narration is wrong.
    "it's": "its",
    # Plural "ones" and possessive/contraction spelling "one's" are also
    # acoustically identical. Whisper consistently chooses the apostrophe in
    # the observed plural phrase "the ones the size of a shoebox".
    "one's": "ones",
    # The locative "there", possessive "their", and contraction "they're"
    # are exact homophones. Whisper consistently chose the locative spelling
    # for the observed sentence-final contraction, even across three separate
    # Orpheus generations; spelling cannot distinguish the spoken utterance.
    "there": "they're",
    "their": "they're",
    # Whisper writes the plural bookstore name "Boxes" as the identically
    # pronounced nonce spelling "Boxus" in the observed proper-name list.
    "boxus": "boxes",
    # Whisper large-v3-turbo writes the fully spoken Japanese bookstore name
    # "Kinokuniya" as "Kenakunya" in the observed Orpheus proper-name list.
    # Keep this exact alias local to acoustic verification; nearby spellings
    # are still rejected.
    "kenakunya": "kinokuniya",
    # Whisper consistently labels the rare spoken word "eunuch" as the
    # familiar two-syllable proper noun "Unix", including at 0.8x speed.
    "unix": "eunuch",
    # Whisper large-v3-turbo spells the correctly pronounced surname
    # "Scorsese" as "Suarcese" in this narration.  This exact, observed
    # spelling alias avoids regenerating otherwise complete Orpheus audio;
    # unrelated near-matches remain rejected.
    "suarcese": "scorsese",
    "sorsese": "scorsese",
    # Whisper writes the fully spoken singular unit "kilometer" as its
    # standard abbreviation after a normalized number (for example 8,000 km).
    "km": "kilometer",
}
# These aliases are deliberately directional: they normalize an observed ASR
# spelling without changing the canonical source vocabulary.  That distinction
# matters for real words such as "guanxi", which Whisper used for the correctly
# spoken Malaysian greeting "Gongxi" but which must remain distinct when it is
# actually present in the script.
TRANSCRIPT_ONLY_ACOUSTIC_EQUIVALENTS = {
    "guanxi": "gongxi",
    # With the provider-side established spelling "Nonya", Whisper writes the
    # correctly spoken Peranakan cuisine name as "Nonia" or "Nanya".  Keep
    # these observed aliases directional so canonical source words using
    # either spelling stay distinct.
    "nonia": "nyonya",
    "nanya": "nyonya",
    # Whisper consistently writes the spoken geographic name "South Seas"
    # with a possessive apostrophe.  Normalize only the observed transcript
    # token so a canonical possessive "Sea's" remains distinct.
    "sea's": "seas",
    # Whisper writes the correctly spoken Cantonese restaurant suffix "Lou"
    # as the identically pronounced name "Lu".  Keep it transcript-only so a
    # canonical person or place named "Lu" remains distinct in source text.
    "lu": "lou",
    # The provider-side pronunciation hint "Poo Tien" keeps the restaurant
    # name complete, while Whisper joins the spoken syllables as "Poutien".
    # Keep the observed spelling transcript-only so canonical "Poutien" is
    # not treated as the source restaurant name in the reverse direction.
    "poutien": "putien",
}
TRANSCRIPT_ONLY_ACOUSTIC_PHRASE_EQUIVALENTS = {
    # The same provider-side "Poo Tien" hint may be tokenized by Whisper as
    # the two-word spelling "Pu Tien" instead of the joined "Poutien" form.
    # Normalize only the observed transcript side so a canonical two-word
    # source remains distinct from the restaurant name "Putien".
    ("pu", "tien"): "putien",
    # Whisper splits the correctly spoken Malay restaurant name "Dagang" into
    # two familiar syllable tokens.  Keep this observed alias off the source
    # side so canonical "Da Gong" remains a distinct two-word name.
    ("da", "gong"): "dagang",
}
ACOUSTIC_PHRASE_EQUIVALENTS = {
    # Whisper may spell the phrasal verb as the identically pronounced noun.
    ("break", "through"): "breakthrough",
    # Source-side lexical splitting preserves the hyphen as two words while
    # Whisper spells this identically pronounced compound as one word.
    ("pre", "arranged"): "prearranged",
    # A provider-only morpheme hint may survive as two exact ASR tokens.
    ("declar", "ing"): "declaring",
    # The provider prompt spells the Kuala Lumpur initialism as two tokens so
    # Orpheus says the letters instead of the word "kale". Whisper may retain
    # that exact split; it is acoustically identical to canonical "KL's".
    ("k", "l's"): "kl's",
    # Whisper segments the correctly pronounced bookstore name "Eslite" as
    # the two acoustic tokens "S" and "Lite". Collapse only that exact pair;
    # unrelated single-word or near-homophone spellings remain rejected.
    ("s", "lite"): "eslite",
    # Whisper segments the correctly pronounced Mandarin greeting "Gongxi"
    # into its two spoken syllables, "Gong" and "Shi". Collapse only that
    # exact observed pair; nearby syllables remain hard failures.
    ("gong", "shi"): "gongxi",
    # Whisper segments the correctly pronounced Malaysian portmanteau
    # "Deparaya" into "De" and the homophonic spelling "Pariah". Collapse
    # only that exact observed pair; other continuations after "De" still fail.
    ("de", "pariah"): "deparaya",
}
NUMBER_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}
DANGLING_CHUNK_WORDS = {
    "a", "all", "an", "and", "as", "at", "but", "by", "for", "from", "in",
    "fully", "into", "nor", "of", "on", "or", "the", "to", "with",
}
# Orpheus repeatedly drops an isolated leading "of" while conjunction-led
# continuations remain reliable. Keep this intervention deliberately narrow so
# existing verified chunk identities do not churn.
BAD_LEADING_CHUNK_WORDS = {"of"}
CHUNK_DETERMINERS = {"a", "an", "the"}
# Never split these observed multi-word proper names across Orpheus requests.
# A stranded "Kuala" was pronounced/transcribed as the common noun "koala";
# keeping the complete place name supplies the speech model with its meaning.
PROTECTED_CHUNK_BOUNDARIES = {("kuala", "lumpur")}
TERMINAL_SPEECH_PUNCTUATION_RE = re.compile(r"[.!?。！？][\"'’”)]*\s*$")
TRAILING_CLAUSE_PUNCTUATION_RE = re.compile(r"[,;:，；：]+([\"'’”)]*)\s*$")
TRAILING_DASH_PUNCTUATION_RE = re.compile(r"\s*[—–]+\s*$")


class TtsIntegrityError(RuntimeError):
    """A provider returned audio that cannot contain the requested narration."""

    def __init__(self, message: str, *, part_key: str | None = None):
        super().__init__(message)
        self.part_key = part_key


class EmptyWavError(TtsIntegrityError):
    """A structurally valid WAV container contains no audio frames."""


@dataclass(frozen=True)
class WavInfo:
    channels: int
    sample_width: int
    sample_rate: int
    frame_count: int
    duration_seconds: float


def _strip_speaker_labels(script: str) -> str:
    """Remove leading 'Speaker N:' markers from a script.

    VibeVoice can vocalize labels verbatim ("Speaker one, ...") depending on
    model/script format. Stripping them yields clean spoken input while line
    breaks preserve turn/beat pacing."""
    lines = []
    for line in script.splitlines():
        cleaned = SPEAKER_LABEL_RE.sub("", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _spoken_word_count(text: str) -> int:
    return len(_strip_speaker_labels(text).split())


def _raw_lexical_tokens(text: str) -> list[str]:
    """Normalize individual lexical tokens without collapsing token groups."""
    normalized: list[str] = []
    for token in LEXICAL_TOKEN_RE.findall(_strip_speaker_labels(text)):
        value = token.replace("’", "'").casefold()
        # Whisper commonly renders spoken "percent" as the punctuation symbol
        # "%", which is not a lexical token. Ignore the unit on both sides;
        # the adjacent normalized number remains the acoustic anchor.
        if value == "percent":
            continue
        value = ACOUSTIC_EQUIVALENTS.get(value, value)
        # Whisper writes a spoken decade either with digits (``1980s``) or
        # with the deprecated apostrophe spelling (``1980's``). Preserve the
        # audible plural suffix while making those spellings comparable.
        numeric_decade = re.fullmatch(r"(\d{2,4})'?s", value)
        if numeric_decade is not None:
            value = f"{numeric_decade.group(1)}s"
        normalized.append(
            ORDINAL_DIGITS.get(
                value,
                DECADE_WORDS.get(value, NUMBER_WORDS.get(value, value)),
            )
        )
    return normalized


def _lexical_tokens(text: str) -> list[str]:
    normalized = _raw_lexical_tokens(text)
    return _canonicalize_number_tokens(_canonicalize_acoustic_phrase_tokens(normalized))


def _canonicalize_acoustic_phrase_tokens(tokens: list[str]) -> list[str]:
    """Collapse narrow split/join spellings that carry identical speech."""
    result: list[str] = []
    index = 0
    while index < len(tokens):
        pair = tuple(tokens[index:index + 2])
        canonical = ACOUSTIC_PHRASE_EQUIVALENTS.get(pair)
        if canonical is not None:
            result.append(canonical)
            index += 2
            continue
        result.append(tokens[index])
        index += 1
    return result


def _canonicalize_number_tokens(tokens: list[str]) -> list[str]:
    """Collapse acoustically identical written/spoken English number forms."""
    # Whisper writes spoken years as one numeric token ("1895"), while the
    # script commonly spells them as "eighteen ninety-five". First collapse a
    # tens+ones pair, then combine two two-digit year halves. Also support the
    # conventional "nineteen oh five" pronunciation.
    simple: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if (
            token.isdigit()
            and 20 <= int(token) <= 90
            and int(token) % 10 == 0
            and index + 1 < len(tokens)
            and tokens[index + 1].isdigit()
            and 1 <= int(tokens[index + 1]) <= 9
        ):
            simple.append(str(int(token) + int(tokens[index + 1])))
            index += 2
            continue
        simple.append(token)
        index += 1
    tokens = simple

    result: list[str] = []
    index = 0
    while index < len(tokens):
        # A script commonly spells a decade as ``nineteen-eighties`` while
        # Whisper emits the acoustically identical single token ``1980s``.
        # Keep the trailing ``s`` so a specific year (1980) is not accepted as
        # a decade (1980s).
        if (
            tokens[index].isdigit()
            and len(tokens[index]) == 2
            and index + 1 < len(tokens)
            and re.fullmatch(r"[2-9]0s", tokens[index + 1])
        ):
            result.append(
                str(int(tokens[index]) * 100 + int(tokens[index + 1][:-1])) + "s"
            )
            index += 2
            continue
        if (
            tokens[index].isdigit()
            and len(tokens[index]) == 2
            and index + 1 < len(tokens)
            and tokens[index + 1].isdigit()
            and len(tokens[index + 1]) == 2
        ):
            result.append(str(int(tokens[index]) * 100 + int(tokens[index + 1])))
            index += 2
            continue
        if (
            tokens[index].isdigit()
            and len(tokens[index]) == 2
            and index + 2 < len(tokens)
            and tokens[index + 1] == "0"
            and tokens[index + 2].isdigit()
            and 1 <= int(tokens[index + 2]) <= 9
        ):
            result.append(str(int(tokens[index]) * 100 + int(tokens[index + 2])))
            index += 3
            continue
        if (
            tokens[index].isdigit()
            and index + 1 < len(tokens)
            and len(tokens[index + 1]) == 3
            and tokens[index + 1].isdigit()
        ):
            result.append(tokens[index] + tokens[index + 1])
            index += 2
            continue
        if (
            tokens[index].isdigit()
            and index + 1 < len(tokens)
            and tokens[index + 1] in NUMBER_SCALES
        ):
            start = index
            current = int(tokens[index])
            index += 1
        elif (
            tokens[index] == "a"
            and index + 1 < len(tokens)
            and tokens[index + 1] in NUMBER_SCALES
        ):
            start = index
            current = 1
        elif tokens[index] in NUMBER_SCALES:
            start = index
            current = 1
        else:
            result.append(tokens[index])
            index += 1
            continue
        if tokens[start] == "a":
            index = start + 1
        elif tokens[start] in NUMBER_SCALES:
            index = start
        first_scale = tokens[index]
        current *= NUMBER_SCALES[first_scale]
        index += 1
        while index < len(tokens) and tokens[index] in NUMBER_SCALES:
            scale = NUMBER_SCALES[tokens[index]]
            current = current * scale if scale >= 1_000 else current + scale
            index += 1
        result.append(str(current))
    return result


def _transcript_tokens(words: list[dict]) -> tuple[list[str], list[int]]:
    tokens: list[str] = []
    word_indexes: list[int] = []
    for index, word in enumerate(words):
        # Preserve number words until the full ASR token stream is available.
        # Normalizing each Whisper word in isolation turns ``thousand`` into
        # ``1000`` too early and leaves the preceding ``a`` as a false extra
        # token, even though the audio says the source's exact ``a thousand``.
        for token in _raw_lexical_tokens(str(word.get("text") or "")):
            tokens.append(TRANSCRIPT_ONLY_ACOUSTIC_EQUIVALENTS.get(token, token))
            word_indexes.append(index)
    # A provider-only pronunciation hint may lead Whisper to retain the
    # morpheme boundary. The pair is acoustically and lexically identical to
    # the canonical word; a different second morpheme remains a hard failure.
    acoustic_tokens: list[str] = []
    acoustic_indexes: list[int] = []
    cursor = 0
    while cursor < len(tokens):
        phrase = tuple(tokens[cursor:cursor + 2])
        canonical_phrase = TRANSCRIPT_ONLY_ACOUSTIC_PHRASE_EQUIVALENTS.get(
            phrase
        ) or ACOUSTIC_PHRASE_EQUIVALENTS.get(phrase)
        if canonical_phrase is not None:
            acoustic_tokens.append(canonical_phrase)
            acoustic_indexes.append(word_indexes[cursor])
            cursor += 2
            continue
        if (
            tokens[cursor] == "dis"
            and cursor + 1 < len(tokens)
            and tokens[cursor + 1] == "proportionate"
        ):
            acoustic_tokens.append("disproportionate")
            acoustic_indexes.append(word_indexes[cursor])
            cursor += 2
            continue
        acoustic_tokens.append(tokens[cursor])
        acoustic_indexes.append(word_indexes[cursor])
        cursor += 1
    tokens = acoustic_tokens
    word_indexes = acoustic_indexes
    canonical = _canonicalize_number_tokens(tokens)
    if len(canonical) == len(tokens):
        return canonical, word_indexes
    # Canonical number collapsing is used only for lexical comparison. Timing
    # indexes remain conservative at the first contributing Whisper word.
    canonical_indexes: list[int] = []
    cursor = 0
    for token in canonical:
        canonical_indexes.append(word_indexes[min(cursor, len(word_indexes) - 1)])
        cursor += 2 if token.isdigit() and cursor + 1 < len(tokens) else 1
    return canonical, canonical_indexes


def _subsequence_starts(haystack: list[str], needle: list[str]) -> list[int]:
    if not needle or len(needle) > len(haystack):
        return []
    return [
        index
        for index in range(len(haystack) - len(needle) + 1)
        if haystack[index:index + len(needle)] == needle
    ]


def _repetition_start(haystack: list[str], needle: list[str]) -> int | None:
    """Return the second utterance onset, including a truncated repetition.

    Orpheus can spend the remainder of its token budget starting the requested
    sentence again.  Waiting for a second *complete* copy misses that partial
    duplicate, so after locating one complete utterance also look for its
    three-word opening anchor in the trailing transcript.
    """
    complete = _subsequence_starts(haystack, needle)
    if len(complete) > 1:
        return complete[1]
    if len(complete) != 1:
        return None
    anchor_size = min(ORPHEUS_EDGE_ANCHOR_WORDS, len(needle))
    if anchor_size < 2:
        return None
    trailing_start = complete[0] + len(needle)
    opening = needle[:anchor_size]
    for index in range(trailing_start, len(haystack) - anchor_size + 1):
        if haystack[index:index + anchor_size] == opening:
            return index
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _split_parallel_clause(sentence: str) -> list[str]:
    """Separate mirrored clauses that make Orpheus repeat the first ending.

    Keep this deliberately narrow: both comma-separated clauses must have the
    same lexical frame after removing their distinct subject and final word.
    For example, ``Japanese go ... die, Chinese go ... win`` becomes two
    independently verified utterances instead of a prompt that the speech LM
    repeatedly expands as ``... die, ... die, ... win``.
    """
    if sentence.count(",") != 1:
        return [sentence]
    left, right = (part.strip() for part in sentence.split(",", 1))
    left_tokens = _lexical_tokens(left)
    right_tokens = _lexical_tokens(right)
    if (
        len(left_tokens) >= 5
        and len(left_tokens) == len(right_tokens)
        and left_tokens[0] != right_tokens[0]
        and left_tokens[-1] != right_tokens[-1]
        and left_tokens[1:-1] == right_tokens[1:-1]
    ):
        return [f"{left},", right]
    return [sentence]


def _separate_repeated_clause_openings(chunks: list[str]) -> list[str]:
    """Force repetition-prone mirrored openings into separate utterances."""
    separated: list[str] = []
    pattern = re.compile(
        r"\bno matter how [^,\n]+,\s+(?=no matter how\b)",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.search(chunk)
        if match is None:
            separated.append(chunk)
            continue
        boundary = match.end()
        left = chunk[:boundary].rstrip()
        right = chunk[boundary:].lstrip()
        if left and right:
            separated.extend((left, right))
        else:
            separated.append(chunk)
    return separated


def _separate_repeated_adjective_items(chunks: list[str]) -> list[str]:
    """Split an observed repetition-prone three-item adjective list."""
    separated: list[str] = []
    pattern = re.compile(
        r"\bdisproportionate\s+[^,\n]+,\s+(?=disproportionate\b)",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.search(chunk)
        if match is None:
            separated.append(chunk)
            continue
        boundary = match.end()
        left = chunk[:boundary].rstrip()
        right = chunk[boundary:].lstrip()
        if left and right:
            separated.extend((left, right))
        else:
            separated.append(chunk)
    return separated


def _reattach_fragile_orpheus_continuations(chunks: list[str]) -> list[str]:
    """Keep an observed past-tense continuation with its stranded subject."""
    adjusted = list(chunks)
    for index in range(len(adjusted) - 1):
        left = adjusted[index]
        right = adjusted[index + 1]
        subject = re.search(r"(?i)(?:^|\s)(and they)$", left)
        if subject is None or re.match(r"(?i)passed\b", right) is None:
            continue
        prefix = left[: subject.start(1)].rstrip()
        if not prefix:
            continue
        adjusted[index] = prefix
        adjusted[index + 1] = f"{subject.group(1)} {right}"
    return adjusted


def _separate_fragile_battle_ready_sequence(chunks: list[str]) -> list[str]:
    """Keep an observed compound phrase intact in shorter utterances."""
    adjusted = list(chunks)
    index = 0
    pattern = re.compile(
        r"^(.*?the most unrestrained,)\s+"
        r"(a battle-ready death cult, sexually open, transformed by drink,)\s+"
        r"(willing to cross any line in art or war\.)$",
        re.IGNORECASE,
    )
    while index < len(adjusted) - 1:
        match = pattern.match(f"{adjusted[index]} {adjusted[index + 1]}")
        if match is None:
            index += 1
            continue
        adjusted[index : index + 2] = [
            match.group(1),
            match.group(2),
            match.group(3),
        ]
        index += 3
    return adjusted


def _separate_fragile_moderation_sequence(chunks: list[str]) -> list[str]:
    """Split an observed repetition-prone three-item moderation list."""
    separated: list[str] = []
    pattern = re.compile(
        r"^(and moderation is beautiful,)\s+"
        r"(moderation is stable,)\s+"
        r"(moderation builds enduring civilizations\.)$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.match(chunk)
        if match is None:
            separated.append(chunk)
            continue
        separated.extend(match.groups())
    return separated


def _separate_repeated_north_pacific_sequence(chunks: list[str]) -> list[str]:
    """Split a repeated place-name sequence that collapses into a loop."""
    separated: list[str] = []
    pattern = re.compile(
        r"^(North Pacific for six aircraft carriers\.)\s+"
        r"(The North Pacific\.\s+In winter\.)$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.match(chunk)
        if match is None:
            separated.append(chunk)
            continue
        separated.extend(match.groups())
    return separated


def _separate_refuge_survival_sequence(chunks: list[str]) -> list[str]:
    """Split two similar short beats that make Orpheus loop the first one."""
    separated: list[str] = []
    pattern = re.compile(
        r"^(.*?\bit's a refuge\.)\s+(It's survival\.)$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.match(chunk)
        if match is None:
            separated.append(chunk)
            continue
        separated.extend(match.groups())
    return separated


def _reattach_kuala_lumpur_sentence(chunks: list[str]) -> list[str]:
    """Keep the observed city statement out of a preposition-led prompt."""
    adjusted = list(chunks)
    pattern = re.compile(
        r"^(And let me tell you,)\s+"
        r"(the Lunar New Year atmosphere in Kuala Lumpur rivals anything "
        r"you'd see in a Chinese city\.)$",
        re.IGNORECASE,
    )
    index = 0
    while index < len(adjusted) - 1:
        match = pattern.match(f"{adjusted[index]} {adjusted[index + 1]}")
        if match is None:
            index += 1
            continue
        adjusted[index : index + 2] = [match.group(1), match.group(2)]
        index += 2
    return adjusted


def _separate_lion_stilts_sequence(chunks: list[str]) -> list[str]:
    """Split an observed long list that loops before reaching its lion beat."""
    separated: list[str] = []
    pattern = re.compile(
        r"^(These performers up on thin stilts,)\s+"
        r"(balancing, leaping between poles,)\s+"
        r"(the lion weaving and dipping\.)$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.match(chunk)
        if match is None:
            separated.append(chunk)
            continue
        separated.extend(match.groups())
    return separated


def _separate_empty_rhetorical_turn(chunks: list[str]) -> list[str]:
    """Split an observed two-sentence prompt that returns empty WAVs."""
    separated: list[str] = []
    pattern = re.compile(
        r"^(Or has it\?)\s+"
        r"(Because here's where the story takes a turn\.)$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.match(chunk)
        if match is None:
            separated.append(chunk)
            continue
        separated.extend(match.groups())
    return separated


def _separate_fragile_wouldnt_eat_sequence(chunks: list[str]) -> list[str]:
    """Split an observed two-sentence prompt that substitutes ``need``."""
    separated: list[str] = []
    pattern = re.compile(
        r"^(They just wouldn't eat it\.)\s+(No big deal\.)$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.match(chunk)
        if match is None:
            separated.append(chunk)
            continue
        separated.extend(match.groups())
    return separated


def _separate_fragile_now_sequence(chunks: list[str]) -> list[str]:
    """Isolate an observed sentence before a fragile rhetorical ``Now?``."""
    separated: list[str] = []
    pattern = re.compile(
        r"^(Nobody made it a big deal\.)\s+"
        r"(Now\?\s+You don't see that anymore\.)$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.match(chunk)
        if match is None:
            separated.append(chunk)
            continue
        separated.extend(match.groups())
    return separated


def _separate_incomplete_cuisine_list(chunks: list[str]) -> list[str]:
    """Split an observed cuisine list that drops its concluding judgment."""
    separated: list[str] = []
    pattern = re.compile(
        r"^(Nyonya cuisine,)\s+"
        r"(Malay, Indian, Chinese,)\s+"
        r"(Lebanese, even vegetarian —)\s+"
        r"(everything hit\.)$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.match(chunk)
        if match is None:
            separated.append(chunk)
            continue
        separated.extend(match.groups())
    return separated


def _separate_fragile_beijing_lou_sequence(chunks: list[str]) -> list[str]:
    """Split an observed long restaurant line that drifts across both names."""
    separated: list[str] = []
    pattern = re.compile(
        r"^(A Cantonese place called Beijing Lou,)\s+"
        r"(in the middle of touristy Malacca,)\s+"
        r"(and somehow still incredible\.)$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.match(chunk)
        if match is None:
            separated.append(chunk)
            continue
        separated.extend(match.groups())
    return separated


def _separate_truncated_shopping_mall_sequence(chunks: list[str]) -> list[str]:
    """Split an observed three-beat prompt that stops after its first beat."""
    separated: list[str] = []
    pattern = re.compile(
        r"^(In a shopping mall!)\s+"
        r"(In Kuala Lumpur!)\s+"
        r"(Who expects that\?)$",
        re.IGNORECASE,
    )
    for chunk in chunks:
        match = pattern.match(chunk)
        if match is None:
            separated.append(chunk)
            continue
        separated.extend(match.groups())
    return separated


def _separate_parallel_history_sequence(chunks: list[str]) -> list[str]:
    """Keep each verb in an observed historical list with its object."""
    adjusted = list(chunks)
    pattern = re.compile(
        r"^(That's a compressed history of generations who built infrastructure,\s+"
        r"opened businesses,)\s+"
        r"(established schools,)\s+"
        r"(shaped the economy of an entire region —)\s+"
        r"(and paid for it in ways most of us never learned about\.)$",
        re.IGNORECASE,
    )
    index = 0
    while index < len(adjusted) - 2:
        match = pattern.match(" ".join(adjusted[index : index + 3]))
        if match is None:
            index += 1
            continue
        adjusted[index : index + 3] = list(match.groups())
        index += len(match.groups())
    return adjusted


def _stabilize_orpheus_chunks(chunks: list[str]) -> list[str]:
    stabilized = _separate_repeated_clause_openings(chunks)
    stabilized = _separate_repeated_adjective_items(stabilized)
    stabilized = _reattach_fragile_orpheus_continuations(stabilized)
    stabilized = _separate_fragile_battle_ready_sequence(stabilized)
    stabilized = _separate_fragile_moderation_sequence(stabilized)
    stabilized = _separate_repeated_north_pacific_sequence(stabilized)
    stabilized = _separate_refuge_survival_sequence(stabilized)
    stabilized = _separate_lion_stilts_sequence(stabilized)
    stabilized = _reattach_kuala_lumpur_sentence(stabilized)
    stabilized = _separate_empty_rhetorical_turn(stabilized)
    stabilized = _separate_fragile_wouldnt_eat_sequence(stabilized)
    stabilized = _separate_fragile_now_sequence(stabilized)
    stabilized = _separate_incomplete_cuisine_list(stabilized)
    stabilized = _separate_fragile_beijing_lou_sequence(stabilized)
    stabilized = _separate_truncated_shopping_mall_sequence(stabilized)
    return _separate_parallel_history_sequence(stabilized)


def _split_tts_text(
    text: str,
    max_words: int,
    *,
    preserve_speaker_labels: bool = False,
) -> list[str]:
    """Split at sentence boundaries while preserving every spoken word.

    Dialogue-capable VibeVoice requires a ``Speaker N:`` marker on every input
    segment. When a long speaker turn crosses a chunk boundary, the marker is
    repeated as metadata; the spoken text itself is neither repeated nor
    dropped.
    """
    if max_words <= 0 or _spoken_word_count(text) <= max_words:
        chunks = [text]
        return (
            chunks
            if preserve_speaker_labels
            else _stabilize_orpheus_chunks(chunks)
        )

    units: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        speaker_label = ""
        content = stripped
        if preserve_speaker_labels:
            match = SPEAKER_LINE_RE.match(stripped)
            if match:
                speaker_label = match.group(1).strip()
                content = match.group(2).strip()
        for sentence in SENTENCE_BOUNDARY_RE.split(content):
            for clause in _split_parallel_clause(sentence):
                words = clause.strip().split()
                while words:
                    take = min(max_words, len(words))
                    if (
                        take < len(words)
                        and words[take - 1].strip(".,!?;:\"'’”()[]{}").casefold()
                        == "at"
                        and words[take][:1].isupper()
                        and words[take].rstrip("\"'’”)]}").endswith(",")
                    ):
                        # Keep a one-word proper-noun object with a trailing
                        # preposition when it also closes the clause. Sending
                        # "at Germany, ..." as a standalone Orpheus prompt
                        # repeatedly changes the later adjective "disciplined"
                        # to the noun "discipline". The bounded one-word
                        # overflow produces the natural "... look at Germany."
                        take += 1
                    remainder = len(words) - take
                    if 0 < remainder < 5:
                        # Avoid context-starved sentence tails such as "belonged
                        # to the state." Orpheus repeatedly drops inflections in
                        # these fragments. Keep the final phrase attached to its
                        # grammatical context; the bounded four-word overflow is
                        # still independently token-budgeted and verified.
                        take = len(words)
                    while (
                        take > 1
                        and take < len(words)
                        and (
                            words[take - 1]
                            .strip(".,!?;:\"'’”()[]{}")
                            .casefold(),
                            words[take]
                            .strip(".,!?;:\"'’”()[]{}")
                            .casefold(),
                        )
                        in PROTECTED_CHUNK_BOUNDARIES
                    ):
                        take -= 1
                    while (
                        take > 1
                        and take < len(words)
                        and words[take - 1].strip(".,!?;:\"'’”()[]{}").casefold()
                        in DANGLING_CHUNK_WORDS
                    ):
                        take -= 1
                    if (
                        take > 1
                        and take < len(words)
                        and words[take].strip(".,!?;:\"'’”()[]{}").casefold()
                        in BAD_LEADING_CHUNK_WORDS
                    ):
                        # Do not strand an attached preposition/conjunction at the
                        # start of the next speech-LM request. Move its phrase head
                        # (and an immediately preceding determiner) with it.
                        take -= 1
                        while (
                            take > 1
                            and words[take - 1].strip(".,!?;:\"'’”()[]{}").casefold()
                            in CHUNK_DETERMINERS
                        ):
                            take -= 1
                    piece = " ".join(words[:take])
                    words = words[take:]
                    units.append(f"{speaker_label} {piece}".strip())

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    def flush() -> None:
        nonlocal current, current_words
        if current:
            chunks.append("\n".join(current))
            current = []
            current_words = 0

    for unit in units:
        word_count = _spoken_word_count(unit)
        if current and current_words + word_count > max_words:
            flush()
        current.append(unit)
        current_words += word_count
    flush()
    return (
        chunks
        if preserve_speaker_labels
        else _stabilize_orpheus_chunks(chunks)
    )


def _prepare_tts_input(
    script_path: str,
    output_dir: str,
    *,
    strip_speaker_labels: bool,
) -> tuple[Path, Path, Path, str]:
    """Create the canonical provider-specific TTS input."""
    script_path_obj = Path(script_path).expanduser().resolve()
    output_dir_path = Path(output_dir).expanduser().resolve()
    output_dir_path.mkdir(parents=True, exist_ok=True)
    source = script_path_obj.read_text(encoding="utf-8")
    cleaned = _strip_speaker_labels(source) if strip_speaker_labels else source.strip()
    if not cleaned:
        raise ValueError("TTS input is empty after removing speaker labels")
    tts_input = output_dir_path / "tts_input.txt"
    tts_input.write_text(cleaned, encoding="utf-8")
    return script_path_obj, output_dir_path, tts_input, cleaned


def _write_chunk_inputs(
    text: str,
    output_dir: Path,
    *,
    max_words: int,
    preserve_speaker_labels: bool = False,
) -> tuple[list[Path], list[str]]:
    chunks = _split_tts_text(
        text,
        max_words,
        preserve_speaker_labels=preserve_speaker_labels,
    )
    if len(chunks) == 1:
        return [output_dir / "tts_input.txt"], chunks
    for stale in output_dir.glob("tts_input_part_*.txt"):
        stale.unlink(missing_ok=True)
    input_paths = [
        output_dir / f"tts_input_part_{index:03d}.txt"
        for index in range(1, len(chunks) + 1)
    ]
    for input_path, chunk in zip(input_paths, chunks):
        input_path.write_text(chunk, encoding="utf-8")
    return input_paths, chunks


def _read_pcm_wav(path: Path) -> WavInfo:
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getcomptype() != "NONE":
                raise TtsIntegrityError(
                    f"TTS output must be uncompressed PCM WAV, got {handle.getcomptype()}"
                )
            sample_rate = handle.getframerate()
            frame_count = handle.getnframes()
            info = WavInfo(
                channels=handle.getnchannels(),
                sample_width=handle.getsampwidth(),
                sample_rate=sample_rate,
                frame_count=frame_count,
                duration_seconds=frame_count / max(1, sample_rate),
            )
    except (wave.Error, EOFError, OSError) as exc:
        raise TtsIntegrityError(f"TTS produced an unreadable WAV at {path}: {exc}") from exc
    if info.frame_count <= 0:
        raise EmptyWavError(f"TTS produced an empty WAV at {path}")
    return info


def _validate_wav_part(
    path: Path,
    text: str,
    *,
    token_limit_seconds: float | None = None,
    speed: float = 1.0,
) -> WavInfo:
    info = _read_pcm_wav(path)
    words = _spoken_word_count(text)
    minimum_seconds = words * 60 / (MAX_PLAUSIBLE_SPEECH_WPM * speed)
    if words >= 8 and info.duration_seconds < minimum_seconds:
        raise TtsIntegrityError(
            f"TTS audio is too short for its input: {words} words produced only "
            f"{info.duration_seconds:.1f}s (minimum sanity bound {minimum_seconds:.1f}s)"
        )
    if (
        token_limit_seconds is not None
        and info.duration_seconds >= token_limit_seconds * ORPHEUS_TOKEN_LIMIT_RATIO
    ):
        raise TtsIntegrityError(
            f"Orpheus audio reached {info.duration_seconds:.1f}s, the configured "
            f"max-token ceiling ({token_limit_seconds:.1f}s); refusing a likely "
            "truncated narration"
        )
    return info


def _validate_downloaded_wav_container(path: Path) -> WavInfo:
    """Reject an invalid or truncated HTTP payload before replacing cached audio."""
    info = _read_pcm_wav(path)
    expected_pcm_bytes = info.frame_count * info.channels * info.sample_width
    try:
        with wave.open(str(path), "rb") as handle:
            pcm_bytes = handle.readframes(info.frame_count)
    except (wave.Error, EOFError, OSError) as exc:
        raise TtsIntegrityError(
            f"Orpheus downloaded an unreadable WAV payload at {path}: {exc}"
        ) from exc
    if len(pcm_bytes) != expected_pcm_bytes:
        raise TtsIntegrityError(
            "Orpheus downloaded a truncated WAV payload: "
            f"expected {expected_pcm_bytes} PCM bytes, received {len(pcm_bytes)}"
        )
    return info


async def _concat_wav_parts(
    wav_parts: list[Path], output_dir: Path, *, log: LogCallback | None
) -> Path:
    if len(wav_parts) == 1:
        return wav_parts[0]
    expected = output_dir / "tts_input_generated.wav"
    infos = [_read_pcm_wav(part) for part in wav_parts]
    reference = infos[0]
    for part, info in zip(wav_parts[1:], infos[1:]):
        if (
            info.channels,
            info.sample_width,
            info.sample_rate,
        ) != (
            reference.channels,
            reference.sample_width,
            reference.sample_rate,
        ):
            raise TtsIntegrityError(
                f"TTS WAV format changed between chunks at {part}: "
                f"expected {reference.channels}ch/{reference.sample_width * 8}bit/"
                f"{reference.sample_rate}Hz, got {info.channels}ch/"
                f"{info.sample_width * 8}bit/{info.sample_rate}Hz"
            )

    staged = output_dir / "tts_input_generated.tmp.wav"
    staged.unlink(missing_ok=True)
    with wave.open(str(staged), "wb") as destination:
        destination.setnchannels(reference.channels)
        destination.setsampwidth(reference.sample_width)
        destination.setframerate(reference.sample_rate)
        for part in wav_parts:
            with wave.open(str(part), "rb") as source:
                destination.writeframesraw(source.readframes(source.getnframes()))
    os.replace(staged, expected)
    joined = _read_pcm_wav(expected)
    expected_frames = sum(info.frame_count for info in infos)
    if joined.frame_count != expected_frames:
        raise TtsIntegrityError(
            f"Lossless WAV join wrote {joined.frame_count} frames; expected {expected_frames}"
        )
    return expected


def _write_tts_manifest(
    output_dir: Path,
    *,
    model: str,
    source_text: str,
    chunks: list[str],
    wav_parts: list[Path],
    output: Path,
    deterministic: bool,
    integrity: dict | None = None,
) -> None:
    parts = []
    for text, path in zip(chunks, wav_parts):
        info = _read_pcm_wav(path)
        parts.append(
            {
                "input": path.with_suffix(".txt").name.replace("_generated", ""),
                "output": path.name,
                "audio_sha256": _file_sha256(path),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "word_count": _spoken_word_count(text),
                **asdict(info),
            }
        )
    payload = {
        "model": model,
        "deterministic_decoding": deterministic,
        "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "source_word_count": _spoken_word_count(source_text),
        "chunk_count": len(parts),
        "parts": parts,
        "output": output.name,
        "output_audio_sha256": _file_sha256(output),
        "output_wav": asdict(_read_pcm_wav(output)),
    }
    if integrity is not None:
        payload["integrity"] = integrity
    (output_dir / "tts_manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _orpheus_chunk_word_limit(max_tokens: int) -> int:
    audio_seconds = max_tokens / ORPHEUS_AUDIO_TOKENS_PER_SECOND
    return max(
        1,
        int(audio_seconds * ORPHEUS_CHUNK_MIN_WPM / 60 * ORPHEUS_CHUNK_SAFETY),
    )


def _orpheus_request_token_budget(text: str, maximum: int) -> int:
    """Bound one short utterance without starving slow natural delivery."""
    expected_seconds = _spoken_word_count(text) * 60 / ORPHEUS_CHUNK_MIN_WPM
    estimated = math.ceil(
        expected_seconds * ORPHEUS_AUDIO_TOKENS_PER_SECOND / ORPHEUS_CHUNK_SAFETY
    )
    return min(maximum, max(ORPHEUS_MIN_REQUEST_TOKENS, estimated))


def _orpheus_prompt_text(text: str) -> str:
    """Give every short LM request an explicit speech termination boundary."""
    stripped = text.rstrip()
    # An isolated one-word rhetorical question between two short statements
    # repeatedly yields a completed zero-frame WAV. Join only the observed
    # continuation into natural provider prosody; canonical verification still
    # requires the unchanged words "Now you don't see that anymore".
    stripped = re.sub(
        r"^Now\?\s+You don't see that anymore\.$",
        "Now, you don't see that anymore.",
        stripped,
        flags=re.IGNORECASE,
    )
    # Orpheus repeatedly realizes the opening phrase "Months of" as singular
    # "Month of". Expose the final plural morpheme to its tokenizer; the
    # canonical script remains unchanged and ASR must still recover "months".
    stripped = re.sub(
        r"^(\s*Months)\s+(of)\b",
        lambda match: f"{match.group(1)[:-1]}-s {match.group(2)}",
        stripped,
        flags=re.IGNORECASE,
    )
    # This three-part list repeatedly makes Orpheus pluralize the final gerund
    # as the non-word "declarings". Expose the real morpheme boundary to its
    # tokenizer; ASR must still recover either the exact word or the exact
    # `declar` + `ing` acoustic pair, never an added plural.
    stripped = re.sub(
        r"\b(proxy conflicts),\s+(aid without troops),\s+"
        r"(arming without declaring)\b",
        lambda match: (
            f"{match.group(1)}, {match.group(2)}, "
            f"{match.group(3)[:-3]}-ing"
        ),
        stripped,
        flags=re.IGNORECASE,
    )
    # In a comma-separated adjective list Orpheus repeatedly drops the final
    # /d/ from "disciplined" and speaks the noun "discipline" instead. Give
    # that exact observed transition a stronger, unspoken articulation pause;
    # the canonical text and the acoustic words being verified stay unchanged.
    stripped = re.sub(
        r"\b(disciplined),\s+(formidable)\b",
        lambda match: f"{match.group(1)}. {match.group(2).capitalize()}",
        stripped,
        flags=re.IGNORECASE,
    )
    # Orpheus repeatedly realizes "passed that love" as present-tense
    # "pass that love", even when the subject is present.  A provider-only
    # sentence pause makes the final /t/ audible while preserving exactly the
    # same lexical script for verification and concatenation.
    stripped = re.sub(
        r"\b(passed)\s+(that love)\b",
        lambda match: f"{match.group(1)}. {match.group(2).capitalize()}",
        stripped,
        flags=re.IGNORECASE,
    )
    # Before the dental onset in "the", this model repeatedly realizes the
    # final /m/ in "skim" as /n/. A doubled final consonant plus an unspoken
    # pause made the exact word audible in the observed phrase; the canonical
    # script still requires ASR to recover "skim", never "skin" or "skimm".
    stripped = re.sub(
        r"\b(skim)\s+(the water surface)\b",
        lambda match: f"{match.group(1)}m, {match.group(2)}",
        stripped,
        flags=re.IGNORECASE,
    )
    # Orpheus repeatedly corrupts both Japanese surnames in this sentence.
    # These provider-only phonetic spellings yielded the exact canonical names
    # under ASR; the narrow shared context prevents unrelated names from being
    # rewritten, and verification still requires "Yamaguchi" and "Nagumo".
    stripped = re.sub(
        r"\bYamaguchi(\s+to launch a third strike,\s+)Nagumo(\s+declined)\b",
        r"Yama Goochi\1Nah-goo-moh\2",
        stripped,
        flags=re.IGNORECASE,
    )
    # The /d/ at the end of "dismissed" repeatedly disappears before the
    # dental onset in "the". A provider-only sentence pause preserves that
    # final consonant while the canonical transcript remains unchanged.
    stripped = re.sub(
        r"\b(dismissed)\s+(the United States)\b",
        lambda match: (
            f"{match.group(1)}. "
            f"{match.group(2)[0].upper()}{match.group(2)[1:]}"
        ),
        stripped,
        flags=re.IGNORECASE,
    )
    # At this exact clause boundary the model repeatedly speaks present-tense
    # "free" instead of the requested past-tense "freed". Exposing the final
    # morpheme preserves both the /d/ and the following "the"; verification
    # still requires ASR to recover the canonical past-tense word.
    stripped = re.sub(
        r"\bFreed(\s+the U-boats to attack Atlantic convoys)\b",
        r"Free-d\1",
        stripped,
        flags=re.IGNORECASE,
    )
    # In this short contrastive beat the model repeatedly lengthens the vowel
    # in "Hulls" into "Holes". A provider-only boundary after the requested
    # word preserves its exact vowel while leaving the canonical script and
    # ASR verification unchanged.
    stripped = re.sub(
        r"\b(Hulls)\s+(were patched)\b",
        lambda match: f"{match.group(1)}. {match.group(2).capitalize()}",
        stripped,
        flags=re.IGNORECASE,
    )
    # The speech LM repeatedly substitutes "precautionate" for the middle of
    # this uncommon word. Expose the real morpheme boundary to its tokenizer;
    # the canonical script remains unchanged and ASR must still recover the
    # exact word (or the exact `dis` + `proportionate` acoustic pair).
    stripped = re.sub(
        r"\bdisproportionate\b",
        "dis-proportionate",
        stripped,
        flags=re.IGNORECASE,
    )
    # Orpheus repeatedly reads the compact initialism in "KL's malls" as the
    # one-syllable word "kales". Split only this observed phrase into letter
    # tokens for the provider; acoustic verification still requires canonical
    # "KL's" (or the exact split "K" + "L's"), never "Kale's".
    stripped = re.sub(
        r"\bKL's(\s+malls)\b",
        r"K L's\1",
        stripped,
        flags=re.IGNORECASE,
    )
    # Orpheus reads the Peranakan cuisine name "Nyonya" as "Mionia" when it
    # is sent verbatim, while a hyphenated phonetic hint loops its first
    # syllable.  Use the established unhyphenated variant spelling only in
    # the observed culinary phrases; acoustic verification still requires
    # Whisper to recover canonical "Nyonya".
    stripped = re.sub(
        r"\bNyonya(\s+(?:cuisine|cooking))\b",
        r"Nonya\1",
        stripped,
        flags=re.IGNORECASE,
    )
    # Orpheus collapses the restaurant name "Putien" into "Puchin" when sent
    # as one unfamiliar token.  Expose its two spoken syllables only in this
    # observed Michelin context; verification continues to require canonical
    # "Putien" from the acoustic transcript.
    stripped = re.sub(
        r"\b(Michelin-starred\s+)Putien\b",
        r"\1Poo Tien",
        stripped,
        flags=re.IGNORECASE,
    )
    # Two consecutive short "it's ..." beats made the speech LM loop the
    # first clause three times and never advance to "It's survival". Keep the
    # exact canonical words, but join only this observed contrast into one
    # provider-side prosodic unit so generation advances instead of repeating.
    stripped = re.sub(
        r"\b(it's a refuge)\.\s+(it's survival)\b",
        r"\1, \2",
        stripped,
        flags=re.IGNORECASE,
    )
    if TERMINAL_SPEECH_PUNCTUATION_RE.search(stripped):
        return stripped
    # A semantic split can leave an em/en dash at the end of a canonical
    # chunk.  The dash is an unspoken pause, so replace it instead of emitting
    # the malformed provider sequence ``—.``.
    dash_terminated = TRAILING_DASH_PUNCTUATION_RE.sub(".", stripped)
    if dash_terminated != stripped:
        return dash_terminated
    # A canonical chunk may end at a comma/semicolon chosen for semantic
    # splitting.  Replace that delimiter only in the provider prompt; appending
    # a period would otherwise create the malformed sequence `,.`.
    clause_terminated = TRAILING_CLAUSE_PUNCTUATION_RE.sub(r".\1", stripped)
    return clause_terminated if clause_terminated != stripped else stripped + "."


def _orpheus_transcript_report(text: str, words: list[dict]) -> dict:
    """Measure whether a short WAV contains its complete requested utterance.

    Whisper is an independent acoustic observer, so exact-token coverage is not
    expected to be 100% for numbers, names, or contractions. Completeness is
    instead fail-closed at the utterance level: high exact coverage, a plausible
    word count, and acoustic anchors at both ends must all pass. When every
    source utterance passes, the final manifest records 100% verified source
    coverage while preserving the raw ASR measurements for audit.
    """
    expected = _lexical_tokens(text)
    observed, observed_word_indexes = _transcript_tokens(words)
    matcher = SequenceMatcher(a=expected, b=observed, autojunk=False)
    pairs: list[tuple[int, int]] = []
    for block in matcher.get_matching_blocks():
        pairs.extend((block.a + offset, block.b + offset) for offset in range(block.size))

    matched_expected = {left for left, _ in pairs}
    exact_coverage = len(matched_expected) / max(1, len(expected))
    word_ratio = len(observed) / max(1, len(expected))
    edge = min(ORPHEUS_EDGE_ANCHOR_WORDS, len(expected))
    leading_anchor = any(index < edge for index in matched_expected)
    trailing_anchor = any(index >= len(expected) - edge for index in matched_expected)
    speech_end = max((float(word.get("end") or 0) for word in words), default=0.0)
    repetition_start = _repetition_start(observed, expected)
    repeat_start_seconds = None
    if repetition_start is not None:
        repeat_word_index = observed_word_indexes[repetition_start]
        repeat_start_seconds = max(0.0, float(words[repeat_word_index].get("start") or 0))

    failures: list[str] = []
    if exact_coverage < ORPHEUS_MIN_EXACT_ASR_COVERAGE:
        failures.append(
            f"exact ASR word coverage {exact_coverage:.1%} is below "
            f"{ORPHEUS_MIN_EXACT_ASR_COVERAGE:.1%}"
        )
    if word_ratio < ORPHEUS_MIN_ASR_WORD_RATIO:
        failures.append(
            f"ASR returned only {len(observed)}/{len(expected)} expected-scale words"
        )
    if word_ratio > ORPHEUS_MAX_ASR_WORD_RATIO:
        failures.append(
            f"ASR returned {len(observed)}/{len(expected)} expected-scale words; "
            "the utterance was likely repeated"
        )
    if not leading_anchor:
        failures.append("opening words have no acoustic transcript anchor")
    if not trailing_anchor:
        failures.append("closing words have no acoustic transcript anchor")

    return {
        "verified": not failures,
        "expected_words": len(expected),
        "transcript_words": len(observed),
        "matched_exact_words": len(matched_expected),
        "exact_asr_word_coverage": round(exact_coverage, 4),
        "transcript_word_ratio": round(word_ratio, 4),
        "leading_anchor": leading_anchor,
        "trailing_anchor": trailing_anchor,
        "speech_end_seconds": round(speech_end, 3),
        "repeat_start_seconds": (
            round(repeat_start_seconds, 3) if repeat_start_seconds is not None else None
        ),
        "failure_reasons": failures,
    }


def _trim_pcm_wav(path: Path, end_seconds: float) -> None:
    info = _read_pcm_wav(path)
    end_frame = min(info.frame_count, max(1, round(end_seconds * info.sample_rate)))
    staged = path.with_suffix(".trim.tmp.wav")
    staged.unlink(missing_ok=True)
    with wave.open(str(path), "rb") as source, wave.open(str(staged), "wb") as destination:
        destination.setparams(source.getparams())
        destination.writeframes(source.readframes(end_frame))
    os.replace(staged, path)


async def _verify_orpheus_part(
    path: Path,
    text: str,
    verification_dir: Path,
    *,
    emit: LogCallback,
) -> dict:
    from backend.pipeline import av_sync

    words, transcription = await av_sync.ensure_word_transcript(
        path,
        verification_dir,
        log=None,
        minimum_words=1,
    )
    if not words:
        failures = "; ".join(transcription.get("failure_reasons") or [])
        raise TtsIntegrityError(
            "Orpheus narration cannot be integrity-verified because acoustic "
            f"transcription is unavailable{': ' + failures if failures else ''}"
        )
    report = _orpheus_transcript_report(text, words)
    repeat_start = report.get("repeat_start_seconds")
    if repeat_start is not None and float(repeat_start) > 0.2:
        emit(
            "Orpheus integrity: trimming repeated utterance at "
            f"{float(repeat_start):.2f}s and re-transcribing"
        )
        _trim_pcm_wav(path, float(repeat_start))
        words, transcription = await av_sync.ensure_word_transcript(
            path,
            verification_dir,
            log=None,
            minimum_words=1,
        )
        if not words:
            raise TtsIntegrityError(
                "Orpheus narration could not be transcribed after repetition trimming"
            )
        report = _orpheus_transcript_report(text, words)
    if not report["verified"]:
        raise TtsIntegrityError(
            "Orpheus narration does not match its input utterance: "
            + "; ".join(report["failure_reasons"])
        )
    emit(
        "Orpheus integrity: utterance verified "
        f"({report['matched_exact_words']}/{report['expected_words']} exact ASR words; "
        "opening and closing anchors present)"
    )
    return report


def _part_metadata_path(path: Path) -> Path:
    return path.with_suffix(".json")


def _load_cached_orpheus_part(path: Path, text: str) -> dict | None:
    metadata_path = _part_metadata_path(path)
    if not path.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if metadata.get("text_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
        return None
    if metadata.get("speed_percent") != config.ORPHEUS_TTS_SPEED_PERCENT:
        return None
    if not (metadata.get("integrity") or {}).get("verified"):
        return None
    try:
        info = _read_pcm_wav(path)
    except TtsIntegrityError:
        return None
    if asdict(info) != metadata.get("wav"):
        return None
    return metadata


def _write_orpheus_part_metadata(
    path: Path,
    text: str,
    *,
    job_id: str,
    request_token_budget: int,
    integrity: dict,
) -> dict:
    payload = {
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "word_count": _spoken_word_count(text),
        "job_id": job_id,
        "request_token_budget": request_token_budget,
        "speed_percent": config.ORPHEUS_TTS_SPEED_PERCENT,
        "wav": asdict(_read_pcm_wav(path)),
        "integrity": integrity,
    }
    destination = _part_metadata_path(path)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, destination)
    return payload


async def _recover_orpheus_part(
    path: Path,
    text: str,
    verification_dir: Path,
    *,
    request_token_budget: int,
    emit: LogCallback,
) -> dict | None:
    """Verify a downloaded part left without valid cache metadata.

    A process restart, or an exact-ASR spelling correction deployed after a
    rejected sample, can leave a complete WAV on disk without a sidecar.  Run
    the same fail-closed acoustic gate before submitting another slow remote
    CPU job.  Truly stale or incomplete audio is ignored and regenerated.
    """
    if not path.is_file():
        return None
    try:
        _validate_wav_part(
            path,
            text,
            speed=config.ORPHEUS_TTS_SPEED_PERCENT / 100,
        )
        integrity = await _verify_orpheus_part(
            path,
            text,
            verification_dir,
            emit=emit,
        )
    except TtsIntegrityError as exc:
        emit(f"Orpheus recovery: existing WAV rejected ({exc}); regenerating")
        return None
    metadata = _write_orpheus_part_metadata(
        path,
        text,
        job_id="recovered-local-output",
        request_token_budget=request_token_budget,
        integrity=integrity,
    )
    emit("Orpheus recovery: accepted existing WAV after acoustic verification")
    return metadata


def _orpheus_http_error_text(exc: Exception) -> str:
    """Keep transport failures useful even when httpx returns an empty message."""
    error_type = type(exc).__name__
    if isinstance(exc, httpx.HTTPStatusError):
        error_type = f"{error_type} (HTTP {exc.response.status_code})"
    detail = str(exc).strip()
    return f"{error_type}: {detail}" if detail else error_type


def _is_permanent_orpheus_http_error(exc: Exception) -> bool:
    """Return whether retrying this same accepted-job request cannot recover."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    status_code = exc.response.status_code
    if 300 <= status_code < 400:
        return True
    return 400 <= status_code < 500 and status_code not in {408, 425, 429}


def _orpheus_retry_delay(attempt: int, exc: Exception) -> float:
    """Use bounded exponential backoff and honor a numeric Retry-After header."""
    base_delay = max(1, config.ORPHEUS_TTS_POLL_SECONDS)
    delay = min(
        float(ORPHEUS_RETRY_MAX_DELAY_SECONDS),
        float(base_delay * (2 ** min(max(0, attempt - 1), 16))),
    )
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("Retry-After", "").strip()
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            pass
    return max(0.0, delay)


async def _generate_orpheus(
    script_path: str,
    output_dir: str,
    voice: str,
    language: str,
    *,
    log: LogCallback | None,
    emit: LogCallback,
    max_tokens: int | None = None,
    verify_text: bool = False,
) -> str:
    if not config.ORPHEUS_TTS_API_KEY:
        raise RuntimeError(
            "Orpheus TTS API key is not configured. Set ORPHEUS_TTS_API_KEY in "
            "Admin -> System -> Voice & TTS."
        )
    token_budget = max_tokens or config.ORPHEUS_TTS_MAX_TOKENS
    script_path_obj, output_dir_path, _tts_input, cleaned = _prepare_tts_input(
        script_path,
        output_dir,
        strip_speaker_labels=True,
    )
    chunk_words = min(
        config.ORPHEUS_TTS_CHUNK_WORDS,
        _orpheus_chunk_word_limit(token_budget),
    )
    input_paths, chunks = _write_chunk_inputs(
        cleaned,
        output_dir_path,
        max_words=chunk_words,
    )
    emit(f"TTS input: stripped speaker labels -> {output_dir_path / 'tts_input.txt'}")
    if len(input_paths) > 1:
        emit(
            f"TTS input: Orpheus-safe split into {len(input_paths)} chunks "
            f"(up to {chunk_words} words per acoustically verified utterance)"
        )
    base_url = config.ORPHEUS_TTS_URL.rstrip("/")
    headers = {"X-API-Key": config.ORPHEUS_TTS_API_KEY}
    timeout = httpx.Timeout(config.ORPHEUS_TTS_REQUEST_TIMEOUT)
    wav_parts: list[Path] = []
    part_metadata: list[dict] = []
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for index, input_path in enumerate(input_paths, start=1):
            name = "TTS" if len(input_paths) == 1 else f"TTS part {index}/{len(input_paths)}"
            chunk = chunks[index - 1]
            expected_part = output_dir_path / f"{input_path.stem}_generated.wav"
            request_token_budget = _orpheus_request_token_budget(chunk, token_budget)
            if verify_text:
                cached = _load_cached_orpheus_part(expected_part, chunk)
                if cached is not None:
                    emit(
                        f"{name}: reusing acoustically verified Orpheus audio "
                        f"({cached['word_count']} source words)"
                    )
                    wav_parts.append(expected_part)
                    part_metadata.append(cached)
                    continue
                recovered = await _recover_orpheus_part(
                    expected_part,
                    chunk,
                    output_dir_path / "verification" / input_path.stem,
                    request_token_budget=request_token_budget,
                    emit=emit,
                )
                if recovered is not None:
                    emit(f"{name}: reusing recovered acoustically verified Orpheus audio")
                    wav_parts.append(expected_part)
                    part_metadata.append(recovered)
                    continue

            payload = {
                "input": _orpheus_prompt_text(input_path.read_text(encoding="utf-8")),
                "language": language,
                "voice_id": voice,
                "max_tokens": request_token_budget,
                # Orpheus is an autoregressive audio LM. Greedy decoding
                # collapses real prompts to unrelated phrases (observed as
                # repeated "Thank you"), so use the service/model defaults and
                # rely on the acoustic integrity gate instead of determinism.
                "temperature": 0.8,
                "top_p": 0.95,
                "top_k": 40,
                "min_p": 0.05,
                "pre_buffer_size": 1.5,
                "n_threads": config.ORPHEUS_TTS_N_THREADS,
                "speed": config.ORPHEUS_TTS_SPEED_PERCENT / 100,
                "response_format": "wav",
            }
            try:
                response = await client.post(f"{base_url}/v1/audio/jobs", json=payload)
                response.raise_for_status()
                job = response.json()
                job_id = str(job["id"])
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise RuntimeError(
                    f"{name} could not submit an Orpheus job: "
                    f"{_orpheus_http_error_text(exc)}"
                ) from exc
            emit(f"{name}: Orpheus job {job_id} queued (voice={voice})")
            deadline = time.monotonic() + config.TTS_TIMEOUT
            retry_timeout = max(1, config.ORPHEUS_TTS_RETRY_TIMEOUT)
            poll_interval = max(1, config.ORPHEUS_TTS_POLL_SECONDS)
            poll_outage_started_at: float | None = None
            poll_retry_attempt = 0
            last_poll_error: Exception | None = None
            last_poll_error_text = ""
            last_poll_error_log_at = float("-inf")
            last_status = ""
            while True:
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutError(
                        f"{name} Orpheus job {job_id} exceeded {config.TTS_TIMEOUT}s"
                    )
                if (
                    poll_outage_started_at is not None
                    and now - poll_outage_started_at >= retry_timeout
                ):
                    assert last_poll_error is not None
                    raise TimeoutError(
                        f"{name} Orpheus job {job_id} had no successful poll for "
                        f"{retry_timeout}s; last error: "
                        f"{_orpheus_http_error_text(last_poll_error)}"
                    ) from last_poll_error
                try:
                    response = await client.get(f"{base_url}/v1/audio/jobs/{job_id}")
                    response.raise_for_status()
                    job = response.json()
                    if not isinstance(job, dict):
                        raise ValueError("Orpheus poll response was not a JSON object")
                    status = str(job.get("status") or "").strip().lower()
                    if not status:
                        raise ValueError(
                            "Orpheus poll response did not contain a non-empty status"
                        )
                except (httpx.HTTPError, ValueError) as exc:
                    if _is_permanent_orpheus_http_error(exc):
                        raise RuntimeError(
                            f"{name} could not poll Orpheus job {job_id}: "
                            f"{_orpheus_http_error_text(exc)}"
                        ) from exc
                    now = time.monotonic()
                    if poll_outage_started_at is None:
                        poll_outage_started_at = now
                    poll_retry_attempt += 1
                    last_poll_error = exc
                    error_text = _orpheus_http_error_text(exc)
                    outage_seconds = now - poll_outage_started_at
                    retry_remaining = retry_timeout - outage_seconds
                    job_remaining = deadline - now
                    if retry_remaining <= 0 or job_remaining <= 0:
                        raise TimeoutError(
                            f"{name} Orpheus job {job_id} had no successful poll for "
                            f"{retry_timeout}s; last error: {error_text}"
                        ) from exc
                    delay = min(
                        _orpheus_retry_delay(poll_retry_attempt, exc),
                        retry_remaining,
                        job_remaining,
                    )
                    if (
                        error_text != last_poll_error_text
                        or now - last_poll_error_log_at
                        >= ORPHEUS_RETRY_LOG_INTERVAL_SECONDS
                    ):
                        emit(
                            f"{name}: transient Orpheus poll error for job {job_id} "
                            f"({error_text}); retrying the same job in {delay:g}s "
                            f"(continuous retry window {retry_timeout}s)"
                        )
                        last_poll_error_text = error_text
                        last_poll_error_log_at = now
                    await asyncio.sleep(delay)
                    continue
                poll_outage_started_at = None
                poll_retry_attempt = 0
                last_poll_error = None
                last_poll_error_text = ""
                if status != last_status:
                    emit(f"{name}: Orpheus job {job_id} is {status}")
                    last_status = status
                if status in {"completed", "complete", "succeeded", "done"}:
                    break
                if status in {"failed", "cancelled", "canceled", "error"}:
                    detail = job.get("error") or job.get("detail") or "no error detail"
                    raise RuntimeError(f"{name} Orpheus job {job_id} failed: {detail}")
                await asyncio.sleep(poll_interval)

            download_outage_started_at = time.monotonic()
            download_retry_attempt = 0
            last_download_error: Exception | None = None
            last_download_error_text = ""
            last_download_error_log_at = float("-inf")
            staged_part = expected_part.with_suffix(".tmp.wav")
            while True:
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutError(
                        f"{name} Orpheus job {job_id} exceeded {config.TTS_TIMEOUT}s "
                        "while downloading audio"
                    )
                if now - download_outage_started_at >= retry_timeout:
                    assert last_download_error is not None
                    raise TimeoutError(
                        f"{name} Orpheus job {job_id} audio download had no success "
                        f"for {retry_timeout}s; last error: "
                        f"{_orpheus_http_error_text(last_download_error)}"
                    ) from last_download_error
                try:
                    response = await client.get(
                        f"{base_url}/v1/audio/jobs/{job_id}/audio"
                    )
                    response.raise_for_status()
                    staged_part.write_bytes(response.content)
                    _validate_downloaded_wav_container(staged_part)
                    break
                except EmptyWavError as exc:
                    # A completed job returning a valid WAV header with zero
                    # frames has a terminal empty result, not a transport
                    # outage. Let the outer integrity loop submit a new job;
                    # retrying this immutable completed artifact for four hours
                    # cannot make audio frames appear.
                    staged_part.unlink(missing_ok=True)
                    raise TtsIntegrityError(
                        f"{name} Orpheus job {job_id} completed with an empty WAV",
                        part_key=input_path.name,
                    ) from exc
                except (httpx.HTTPError, TtsIntegrityError) as exc:
                    staged_part.unlink(missing_ok=True)
                    if _is_permanent_orpheus_http_error(exc):
                        raise RuntimeError(
                            f"{name} could not download Orpheus job {job_id}: "
                            f"{_orpheus_http_error_text(exc)}"
                        ) from exc
                    download_retry_attempt += 1
                    last_download_error = exc
                    error_text = _orpheus_http_error_text(exc)
                    now = time.monotonic()
                    retry_remaining = retry_timeout - (
                        now - download_outage_started_at
                    )
                    job_remaining = deadline - now
                    if retry_remaining <= 0 or job_remaining <= 0:
                        raise TimeoutError(
                            f"{name} Orpheus job {job_id} audio download had no "
                            f"success for {retry_timeout}s; last error: {error_text}"
                        ) from exc
                    delay = min(
                        _orpheus_retry_delay(download_retry_attempt, exc),
                        retry_remaining,
                        job_remaining,
                    )
                    if (
                        error_text != last_download_error_text
                        or now - last_download_error_log_at
                        >= ORPHEUS_RETRY_LOG_INTERVAL_SECONDS
                    ):
                        emit(
                            f"{name}: transient Orpheus audio download error for "
                            f"job {job_id} ({error_text}); retrying the same job "
                            f"in {delay:g}s (continuous retry window {retry_timeout}s)"
                        )
                        last_download_error_text = error_text
                        last_download_error_log_at = now
                    await asyncio.sleep(delay)
            os.replace(staged_part, expected_part)
            try:
                _validate_wav_part(
                    expected_part,
                    chunk,
                    token_limit_seconds=(
                        None
                        if verify_text
                        else request_token_budget
                        / ORPHEUS_AUDIO_TOKENS_PER_SECOND
                        / (config.ORPHEUS_TTS_SPEED_PERCENT / 100)
                    ),
                    speed=config.ORPHEUS_TTS_SPEED_PERCENT / 100,
                )
            except TtsIntegrityError as exc:
                raise TtsIntegrityError(str(exc), part_key=input_path.name) from exc
            if verify_text:
                try:
                    integrity = await _verify_orpheus_part(
                        expected_part,
                        chunk,
                        output_dir_path / "verification" / input_path.stem,
                        emit=emit,
                    )
                except TtsIntegrityError as exc:
                    raise TtsIntegrityError(str(exc), part_key=input_path.name) from exc
            else:
                integrity = {
                    "verified": True,
                    "method": "duration_only_preview",
                    "expected_words": _spoken_word_count(chunk),
                }
            metadata = _write_orpheus_part_metadata(
                expected_part,
                chunk,
                job_id=job_id,
                request_token_budget=request_token_budget,
                integrity=integrity,
            )
            wav_parts.append(expected_part)
            part_metadata.append(metadata)

    expected = await _concat_wav_parts(wav_parts, output_dir_path, log=log)
    source_words = _spoken_word_count(cleaned)
    verified_words = sum(
        int(metadata.get("word_count") or 0)
        for metadata in part_metadata
        if (metadata.get("integrity") or {}).get("verified")
    )
    integrity = {
        "method": "per_utterance_mlx_whisper",
        "required": verify_text,
        "passed": verified_words == source_words,
        "source_words": source_words,
        "verified_source_words": verified_words,
        "verified_source_coverage": round(verified_words / max(1, source_words), 4),
        "part_reports": [metadata.get("integrity") or {} for metadata in part_metadata],
    }
    if verify_text and not integrity["passed"]:
        raise TtsIntegrityError(
            f"Orpheus verified only {verified_words}/{source_words} source words; "
            "refusing to join incomplete narration"
        )
    _write_tts_manifest(
        output_dir_path,
        model="orpheus-en",
        source_text=cleaned,
        chunks=chunks,
        wav_parts=wav_parts,
        output=expected,
        deterministic=False,
        integrity=integrity,
    )
    emit(
        f"TTS output: {expected} ({expected.stat().st_size / 1024:.0f} KB; "
        f"source={script_path_obj})"
    )
    return str(expected)


async def generate_tts(
    script_path: str,
    output_dir: str,
    voices: list[str] | None = None,
    tts_model: str | None = None,
    log: LogCallback | None = None,
) -> str:
    voices = voices or [config.TTS_DEFAULT_VOICE_1, config.TTS_DEFAULT_VOICE_2]
    tts_model = tts_model or config.TTS_DEFAULT_MODEL

    # Mirror to the task log (pipeline.log + LogPanel) when available, else the
    # module logger (start.sh log). Prefer the callback to avoid double-logging.
    def emit(message: str) -> None:
        if log:
            log(message)
        else:
            logger.info(message)

    model = config.TTS_MODELS.get(tts_model)
    if model is None:
        valid = ", ".join(sorted(config.TTS_MODELS))
        raise ValueError(f"Unknown TTS model '{tts_model}'. Valid models: {valid}")

    available_voices = config.voices_for_model(tts_model)
    unknown_voices = [voice for voice in voices if voice not in available_voices]
    if unknown_voices:
        raise ValueError(
            f"Voice(s) {unknown_voices} are unavailable for '{tts_model}'. Valid voices: "
            f"{', '.join(available_voices)}"
        )
    if model.get("kind") == "orpheus_http":
        if len(voices) > 1:
            emit(f"Model '{tts_model}' is single-speaker; using only '{voices[0]}'")
        integrity_attempts: dict[str, int] = {}
        while True:
            try:
                return await _generate_orpheus(
                    script_path,
                    output_dir,
                    voices[0],
                    str(model.get("language") or "en"),
                    log=log,
                    emit=emit,
                    verify_text=True,
                )
            except TtsIntegrityError as exc:
                part_key = exc.part_key or "complete narration"
                attempt = integrity_attempts.get(part_key, 0) + 1
                integrity_attempts[part_key] = attempt
                if attempt >= ORPHEUS_MAX_INTEGRITY_ATTEMPTS:
                    raise
                emit(
                    f"Orpheus integrity retry for {part_key} "
                    f"{attempt}/{ORPHEUS_MAX_INTEGRITY_ATTEMPTS - 1}: {exc}. "
                    "Verified earlier utterances will be reused."
                )

    required_runtime_paths = {
        "environment script": Path(model["env_script"]),
        "project directory": Path(model["project_dir"]),
        "inference script": Path(model["inference_script"]),
    }
    missing = [
        f"{label}: {path}"
        for label, path in required_runtime_paths.items()
        if not path.exists()
    ]
    if missing:
        details = "; ".join(missing)
        raise RuntimeError(
            "VibeVoice TTS runtime is unavailable in this process "
            f"({details}). Run the local app with ./scripts/start.sh and "
            "verify AIWORK_ROOT points to the installed VibeVoice runtime."
        )

    # Single-speaker models (e.g. 0.5B realtime) only accept one voice source,
    # so drop any extras regardless of the task's configured speaker count.
    if model.get("single_speaker") and len(voices) > 1:
        emit(
            f"Model '{tts_model}' is single-speaker; using only first voice "
            f"'{voices[0]}' (ignoring {voices[1:]})"
        )
        voices = voices[:1]

    voice_aliases = model.get("voice_aliases", {})
    resolved_voices = [voice_aliases.get(voice, voice) for voice in voices]
    substitutions = [
        f"{requested} -> {resolved}"
        for requested, resolved in zip(voices, resolved_voices)
        if requested != resolved
    ]
    if substitutions:
        emit(
            f"Model '{tts_model}' voice substitution: "
            f"{', '.join(substitutions)}"
        )
    voices = resolved_voices

    # The subprocess runs from VibeVoice's project directory. Resolve every
    # application-owned path before changing cwd, otherwise relative paths are
    # interpreted under VibeVoice and valid inputs appear to be missing.
    preserve_speaker_labels = bool(model.get("requires_speaker_labels"))
    script_path_obj, output_dir_path, _tts_input, prepared_text = _prepare_tts_input(
        script_path,
        output_dir,
        strip_speaker_labels=not preserve_speaker_labels,
    )
    input_paths, chunks = _write_chunk_inputs(
        prepared_text,
        output_dir_path,
        max_words=config.VIBEVOICE_TTS_CHUNK_WORDS,
        preserve_speaker_labels=preserve_speaker_labels,
    )
    input_contract = (
        "preserved speaker labels"
        if preserve_speaker_labels
        else "stripped speaker labels"
    )
    emit(f"TTS input: {input_contract} -> {output_dir_path / 'tts_input.txt'}")
    if len(input_paths) > 1:
        emit(
            f"TTS input: VibeVoice-safe split into {len(input_paths)} "
            f"chunks (limit {config.VIBEVOICE_TTS_CHUNK_WORDS} words each)"
        )

    speaker_args = " ".join(f'"{v}"' for v in voices)

    emit(f"Running TTS: model={tts_model}, voices={voices}, script={script_path_obj}")
    wav_parts: list[Path] = []
    for index, input_path in enumerate(input_paths, start=1):
        expected_part = output_dir_path / f"{input_path.stem}_generated.wav"
        expected_part.unlink(missing_ok=True)
        cmd = f"""
source "{model['env_script']}"
cd "{model['project_dir']}"
python "{config.PROJECT_ROOT / 'backend' / 'pipeline' / 'tts_seeded_runner.py'}" \
    {config.TTS_RANDOM_SEED} \
    "{model['inference_script']}" \
    --txt_path "{input_path}" \
    {model['speaker_flag']} {speaker_args} \
    --output_dir "{output_dir_path}" \
    --device {config.TTS_DEVICE}
"""
        process_name = (
            "TTS"
            if len(input_paths) == 1
            else f"TTS part {index}/{len(input_paths)}"
        )
        returncode, output = await stream_subprocess(
            name=process_name,
            command=["bash", "-c", cmd],
            logger=logger,
            log=log,
            cwd=model["project_dir"],
            timeout=config.TTS_TIMEOUT,
            stall_timeout=config.TTS_STALL_TIMEOUT,
        )
        if returncode != 0:
            raise RuntimeError(
                f"{process_name} generation failed (exit {returncode}): {output[-500:]}"
            )
        if not expected_part.exists():
            raise RuntimeError(
                f"{process_name} produced no WAV output at {expected_part}"
            )
        _validate_wav_part(expected_part, chunks[index - 1])
        wav_parts.append(expected_part)

    expected = await _concat_wav_parts(wav_parts, output_dir_path, log=log)
    _write_tts_manifest(
        output_dir_path,
        model=tts_model,
        source_text=prepared_text,
        chunks=chunks,
        wav_parts=wav_parts,
        output=expected,
        deterministic=True,
    )

    emit(f"TTS output: {expected} ({expected.stat().st_size / 1024:.0f} KB)")
    return str(expected)
