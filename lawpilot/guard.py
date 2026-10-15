"""Treat document text as untrusted data.

Uploaded and retrieved documents can contain text that tries to steer the model
("ignore previous instructions..."). We never execute it: evidence is wrapped in
delimiters the model is told to treat as data, delimiter look-alikes are
neutralised, invisible characters are stripped, and suspicious passages are
flagged so the user can see them.
"""
from __future__ import annotations

import re
from typing import List, Tuple

ZERO_WIDTH = re.compile("[​‌‍⁠﻿‪-‮⁦-⁩]")

SUSPICIOUS = [
    (r"ignore (?:all |any )?(?:previous|prior|above|earlier) (?:instructions|directions|prompts)", "instruction_override"),
    (r"disregard (?:the |all )?(?:system|previous|above) (?:prompt|instructions)", "instruction_override"),
    (r"you are now (?:a|an|the) ", "role_hijack"),
    (r"(?:system|developer) prompt", "prompt_reference"),
    (r"</?\s*(?:system|assistant|evidence|instructions?)\s*>", "delimiter_injection"),
    (r"<\|im_(?:start|end)\|>|\[/?INST\]", "delimiter_injection"),
    (r"(?:reveal|print|output) (?:your|the) (?:instructions|system prompt|api key)", "exfiltration"),
    (r"(?:always|must) (?:cite|state|answer|conclude) that", "answer_steering"),
]
_COMPILED = [(re.compile(p, re.I), name) for p, name in SUSPICIOUS]


def scan(text: str) -> List[str]:
    return sorted({name for rx, name in _COMPILED if rx.search(text or "")})


def sanitize(text: str) -> Tuple[str, List[str]]:
    flags = scan(text)
    if ZERO_WIDTH.search(text or ""):
        flags.append("hidden_characters")
    clean = ZERO_WIDTH.sub("", text or "")
    clean = re.sub(r"<\s*(/?)\s*(system|assistant|evidence|instructions?)\s*>", r"[\1\2]", clean, flags=re.I)
    clean = clean.replace("<|im_start|>", "[im_start]").replace("<|im_end|>", "[im_end]")
    return clean, sorted(set(flags))
