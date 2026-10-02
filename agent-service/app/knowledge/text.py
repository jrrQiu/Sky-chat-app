from __future__ import annotations

import math
import re
from collections import Counter


_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_PUNCT = re.compile(r"[，。；：！？、,.!?;:()\[\]{}<>《》\"'（）\s]+")


def _cjk_bigrams(run: str) -> list[str]:
    if len(run) == 1:
        return [run]
    if len(run) <= 4:
        tokens = [run]
    else:
        tokens = []
    tokens.extend(run)
    tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def tokenize(text: str) -> list[str]:
    normalized = _PUNCT.sub(" ", (text or "").lower())
    tokens: list[str] = _ASCII_WORD.findall(normalized)
    for run in _CJK_RUN.findall(normalized):
        tokens.extend(_cjk_bigrams(run))
    return [token for token in tokens if token]


def sparse_vector(text: str) -> Counter[str]:
    return Counter(tokenize(text))


def cosine_similarity(
    left: Counter[str],
    right: Counter[str],
) -> float:
    if not left or not right:
        return 0.0

    common = left.keys() & right.keys()
    numerator = sum(left[token] * right[token] for token in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def token_count(text: str) -> int:
    return len(tokenize(text))
