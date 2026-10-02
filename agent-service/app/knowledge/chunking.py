from __future__ import annotations

import re

from app.knowledge.models import KnowledgeChunk, PolicyDocument
from app.knowledge.text import token_count


_SECTION_SPLIT = re.compile(r"(?:\r?\n)+|(?<=[。；])\s*")


def split_paragraphs(content: str) -> list[str]:
    parts = [part.strip() for part in _SECTION_SPLIT.split(content or "")]
    return [part for part in parts if part]


def chunk_policy_document(
    document: PolicyDocument,
) -> list[KnowledgeChunk]:
    root = KnowledgeChunk(
        id=f"{document.id}-ROOT",
        document_id=document.id,
        parent_chunk_id=None,
        chunk_index=0,
        content=document.content,
        keywords=list(document.keywords),
        section_path=[document.title],
        page=1,
        source_type=document.source_type,
        token_count=token_count(document.content),
    )
    chunks = [root]
    paragraphs = split_paragraphs(document.content)

    for index, paragraph in enumerate(paragraphs, start=1):
        chunks.append(
            KnowledgeChunk(
                id=f"{document.id}-P{index}",
                document_id=document.id,
                parent_chunk_id=root.id,
                chunk_index=index,
                content=paragraph,
                keywords=list(document.keywords),
                section_path=[document.title, f"第{index}条"],
                page=1,
                source_type=document.source_type,
                token_count=token_count(paragraph),
            )
        )

    return chunks
