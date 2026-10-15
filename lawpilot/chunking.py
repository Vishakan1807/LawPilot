"""Split legal text into retrievable passages that keep page, paragraph and section references.

Judgments are usually numbered by paragraph ("12.", "(12)", "12)"); statutes by
section ("3. Punishment for ..."), with chapters and articles. Paragraphs are
kept intact and grouped into passages of a few hundred words; a passage never
spans two statutory sections.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

TARGET_CHARS = 1200
MAX_CHARS = 1800

PARA_RE = re.compile(r"^\s*(?:\(?(\d{1,3})[.)]\s+|\[(\d{1,3})\]\s*)(?=\S)")
SECTION_RE = re.compile(r"^\s*(?:section\s+)?(\d{1,3}[A-Z]{0,2})\.\s+([A-Z][^\n]{2,120})")
ARTICLE_RE = re.compile(r"^\s*article\s+(\d{1,3}[A-Z]?)\b[.\s-]*(.{0,120})", re.I)
# India Code section openings: "482. Direction for grant of bail to person apprehending arrest .—(1)"
INLINE_SECTION_RE = re.compile(r"(?<=[.;:\])])\s+(?=\d{1,3}[A-Z]{0,2}\.\s+[A-Z][^\n]{2,220}?\s?[.,]?\s?[—–]\s?)")
HEADING_RE = re.compile(r"^\s*((?:CHAPTER|PART)\s+[IVXLC\d]+[A-Z]?\b.*|SCHEDULE\b.*)$", re.I)


@dataclass
class Chunk:
    text: str
    page: Optional[int] = None
    para_ref: Optional[str] = None
    section_ref: Optional[str] = None
    heading: Optional[str] = None


@dataclass
class _Para:
    text: str
    page: Optional[int]
    para_no: Optional[str]
    section: Optional[str]
    heading: Optional[str]


def chunk_pages(pages: List[Tuple[Optional[int], str]], is_statute: bool = False,
                para_numbers: bool = True) -> List[Chunk]:
    """para_numbers=False for summaries/commentary, whose numbered lists are not judgment paragraphs."""
    paras = list(_paragraphs(pages, is_statute, para_numbers))
    chunks: List[Chunk] = []
    buf: List[_Para] = []

    def flush():
        if buf:
            chunks.append(_merge(buf))
            buf.clear()

    for p in paras:
        for piece in _split_long(p):
            size = sum(len(b.text) for b in buf)
            boundary = buf and is_statute and piece.section != buf[-1].section
            if buf and (boundary or size + len(piece.text) > TARGET_CHARS):
                flush()
            buf.append(piece)
    flush()
    return chunks


def _paragraphs(pages, is_statute, para_numbers=True):
    section = heading = None
    for page_no, text in pages:
        blocks = re.split(r"\n\s*\n", text)
        if len(blocks) == 1:
            blocks = _split_on_numbered_lines(text)
        if is_statute:
            # Sections often start mid-paragraph: "...shall apply. 482. Direction for grant of bail ...—(1) When ..."
            blocks = [part for b in blocks for part in INLINE_SECTION_RE.split(re.sub(r"[ \t]*\n[ \t]*", " ", b))]
        for block in blocks:
            block = re.sub(r"[ \t]*\n[ \t]*", " ", block).strip()
            if not block:
                continue
            h = HEADING_RE.match(block)
            if h and len(block) < 140:
                heading = block
                continue
            if is_statute:
                m = SECTION_RE.match(block)
                a = ARTICLE_RE.match(block)
                if m:
                    section = f"Section {m.group(1)}"
                elif a:
                    section = f"Article {a.group(1)}"
            pm = PARA_RE.match(block)
            para_no = (pm.group(1) or pm.group(2)) if pm and para_numbers and not is_statute else None
            yield _Para(block, page_no, para_no, section, heading)


def _split_on_numbered_lines(text: str) -> List[str]:
    """Some extracted text has no blank lines; fall back to numbered-paragraph starts."""
    lines = text.split("\n")
    blocks, cur = [], []
    for line in lines:
        if (PARA_RE.match(line) or SECTION_RE.match(line)) and cur:
            blocks.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def _split_long(p: _Para) -> List[_Para]:
    if len(p.text) <= MAX_CHARS:
        return [p]
    sentences = re.split(r"(?<=[.;:?!])\s+(?=[A-Z(\"'])", p.text)
    out, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) > TARGET_CHARS:
            out.append(cur)
            cur = ""
        cur = (cur + " " + s).strip()
        while len(cur) > MAX_CHARS:
            out.append(cur[:TARGET_CHARS])
            cur = cur[TARGET_CHARS:]
    if cur:
        out.append(cur)
    return [_Para(t, p.page, p.para_no, p.section, p.heading) for t in out]


def _merge(buf: List[_Para]) -> Chunk:
    nums = [b.para_no for b in buf if b.para_no]
    para_ref = None
    if nums:
        para_ref = f"para {nums[0]}" if nums[0] == nums[-1] else f"paras {nums[0]}-{nums[-1]}"
    sections = [b.section for b in buf if b.section]
    section_ref = None
    if sections:
        section_ref = sections[0] if sections[0] == sections[-1] else f"{sections[0]} to {sections[-1]}"
    page = next((b.page for b in buf if b.page is not None), None)
    heading = next((b.heading for b in reversed(buf) if b.heading), None)
    return Chunk(text="\n\n".join(b.text for b in buf), page=page, para_ref=para_ref,
                 section_ref=section_ref, heading=heading)
