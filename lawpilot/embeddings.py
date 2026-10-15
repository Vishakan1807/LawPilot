"""Text embeddings for vector retrieval.

Providers:
  local   - built-in hashing embedder (no download, no API). It captures word,
            phrase, sub-word and legal-concept overlap. It is NOT a neural
            semantic model; treat it as a strong lexical-semantic baseline.
  voyage  - Voyage AI `voyage-law-2`, a model trained on legal text (recommended
            for production; set VOYAGE_API_KEY).
  openai  - OpenAI `text-embedding-3-small` (set OPENAI_API_KEY).

Every chunk records which model embedded it, and retrieval only compares
vectors produced by the active model.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from collections import Counter
from typing import List, Optional

import numpy as np
import requests

log = logging.getLogger("lawpilot.embeddings")

STOPWORDS = set("""
a about above after again against all am an and any are as at be because been before being below between both
but by can could did do does doing down during each few for from further had has have having he her here hers
him his how i if in into is it its itself just me more most my no nor not now of off on once only or other our
out over own same she should so some such than that the their them then there these they this those through to
too under until up very was we were what when where which while who whom why will with would you your shall may
whether also said such upon thereof therein whereas hereby within without
""".split())

# Groups of phrases that refer to the same legal concept. A match adds a shared
# concept feature so that, e.g., "bounced cheque" and "dishonour of cheque" meet.
CONCEPT_GROUPS = {
    "cheque_dishonour": ["cheque bounce", "bounced cheque", "dishonour of cheque", "cheque dishonour",
                         "section 138", "negotiable instruments", "insufficient funds", "funds insufficient"],
    "anticipatory_bail": ["anticipatory bail", "section 438", "section 482 bnss", "pre-arrest bail"],
    "regular_bail": ["regular bail", "section 439", "section 437", "grant of bail", "bail application"],
    "quashing": ["quash", "quashing", "section 482", "inherent powers", "section 528 bnss"],
    "privacy": ["right to privacy", "privacy", "informational privacy", "surveillance"],
    "personal_liberty": ["article 21", "personal liberty", "right to life"],
    "equality": ["article 14", "equality before law", "arbitrariness", "arbitrary"],
    "free_speech": ["article 19", "freedom of speech", "freedom of expression"],
    "writ": ["article 226", "article 32", "writ petition", "mandamus", "certiorari", "habeas corpus"],
    "basic_structure": ["basic structure", "article 368", "constitutional amendment"],
    "arbitration": ["arbitration", "arbitral", "arbitrator", "arbitration agreement", "arbitral award"],
    "limitation": ["limitation", "time-barred", "time barred", "condonation of delay", "section 5 limitation"],
    "dowry_cruelty": ["498a", "498-a", "cruelty", "dowry", "section 85 bns", "section 86 bns"],
    "murder": ["murder", "section 302", "section 103 bns", "culpable homicide"],
    "cheating": ["cheating", "section 420", "section 318 bns", "dishonestly inducing"],
    "maintenance": ["maintenance", "section 125", "section 144 bnss", "alimony"],
    "divorce": ["divorce", "dissolution of marriage", "hindu marriage act", "irretrievable breakdown"],
    "electronic_evidence": ["65b", "section 63 bsa", "electronic record", "electronic evidence"],
    "specific_performance": ["specific performance", "specific relief"],
    "injunction": ["injunction", "temporary injunction", "order 39", "order xxxix"],
    "insolvency": ["insolvency", "ibc", "corporate insolvency", "resolution professional", "section 7 ibc",
                   "section 9 ibc"],
    "consumer": ["consumer", "deficiency in service", "consumer protection"],
    "natural_justice": ["natural justice", "audi alteram partem", "hearing", "bias"],
    "sexual_harassment": ["sexual harassment", "posh", "vishaka"],
    "reservation": ["reservation", "backward classes", "creamy layer", "article 16(4)", "article 15(4)"],
    "service_law": ["service law", "termination of service", "disciplinary proceedings", "departmental enquiry"],
    "property_transfer": ["sale deed", "transfer of property", "conveyance", "title"],
    "tenancy": ["tenant", "landlord", "eviction", "rent control"],
    "stamp_duty": ["stamp duty", "unstamped", "insufficiently stamped", "stamp act"],
    "precedent": ["binding precedent", "article 141", "per incuriam", "stare decisis", "ratio decidendi"],
}

ABBREVIATIONS = [
    (r"\bu/s\.?\s*", "section "),
    (r"\bss?\.\s*(?=\d)", "section "),
    (r"\bsec\.?\s*(?=\d)", "section "),
    (r"\bart\.?\s*(?=\d)", "article "),
    (r"\bo\.\s*(?=[ivx\d])", "order "),
    (r"\br\.\s*(?=\d)", "rule "),
    (r"\bvs?\.?\b", " v "),
]


def normalize(text: str) -> str:
    t = (text or "").lower()
    for pat, rep in ABBREVIATIONS:
        t = re.sub(pat, rep, t)
    return re.sub(r"\s+", " ", t)


def _stem(w: str) -> str:
    for suf in ("ations", "ation", "ments", "ment", "ings", "ing", "ness", "ities", "ity", "ies", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokenize(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9]+(?:\([0-9a-z]+\))*", normalize(text))
    return [_stem(w) for w in words if w not in STOPWORDS and (len(w) > 1 or w.isdigit())]


class EmbeddingError(RuntimeError):
    """The embedding service refused or failed; message is safe to show to admins."""


def _friendly(status: int, text: str) -> str:
    low = text.lower()
    if status == 429 and "payment method" in low:
        return ("Voyage is limiting this account to 3 requests per minute because no payment method is added. "
                "Add one at dash.voyageai.com (Billing), or switch Search embeddings back to Built-in.")
    if status == 429:
        return "The embedding service is rate-limiting requests. Try again in a few minutes."
    if status in (401, 403):
        return "The embedding service rejected the API key. Check it in Corpus admin -> AI settings."
    return f"Embedding service error {status}: {text[:200]}"


class Embedder:
    name = ""
    dim = 0

    def embed_documents(self, texts: List[str]) -> np.ndarray:
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class LocalHashEmbedder(Embedder):
    name = "local-hash-v1"
    dim = 1024

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        norm_text = normalize(text)
        toks = tokenize(text)
        feats = Counter()
        for t in toks:
            feats["w:" + t] += 1.0
            if len(t) >= 6:
                for i in range(len(t) - 3):
                    feats["c:" + t[i:i + 4]] += 0.25
        for a, b in zip(toks, toks[1:]):
            feats["b:" + a + "_" + b] += 0.8
        for concept, phrases in CONCEPT_GROUPS.items():
            hits = sum(norm_text.count(p) for p in phrases)
            if hits:
                feats["k:" + concept] += 2.0 * (1 + math.log(hits))
        for f, count in feats.items():
            h = int.from_bytes(hashlib.md5(f.encode()).digest()[:8], "little")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            v[idx] += sign * (1.0 + math.log(count)) if count >= 1 else sign * count
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    def embed_documents(self, texts):
        return np.vstack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)


class _HttpEmbedder(Embedder):
    url = ""
    batch = 64

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _payload(self, texts, input_type):
        raise NotImplementedError

    def _call(self, texts: List[str], input_type: str) -> np.ndarray:
        out = []
        for i in range(0, len(texts), self.batch):
            part = [t[:16000] for t in texts[i:i + self.batch]]
            for attempt in range(3):
                try:
                    r = requests.post(self.url, json=self._payload(part, input_type),
                                      headers={"Authorization": f"Bearer {self.api_key}"}, timeout=120)
                except requests.RequestException as exc:
                    raise EmbeddingError(f"Could not reach the embedding service: {exc}") from exc
                if r.status_code != 429 or attempt == 2 or "payment method" in r.text.lower():
                    break
                time.sleep(min(30.0, float(r.headers.get("Retry-After") or 5 * (attempt + 1))))
            if r.status_code != 200:
                raise EmbeddingError(_friendly(r.status_code, r.text))
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
        arr = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return arr / norms

    def embed_documents(self, texts):
        return self._call(texts, "document") if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text):
        return self._call([text], "query")[0]


class VoyageEmbedder(_HttpEmbedder):
    name = "voyage-law-2"
    dim = 1024
    url = "https://api.voyageai.com/v1/embeddings"

    def _payload(self, texts, input_type):
        return {"input": texts, "model": "voyage-law-2", "input_type": input_type}


class OpenAIEmbedder(_HttpEmbedder):
    name = "openai-text-embedding-3-small-1024"
    dim = 1024
    url = "https://api.openai.com/v1/embeddings"

    def _payload(self, texts, input_type):
        return {"input": texts, "model": "text-embedding-3-small", "dimensions": 1024}


_lock = threading.Lock()
_cached: Optional[Embedder] = None
_cached_key = None


def get_embedder(cfg) -> Embedder:
    global _cached, _cached_key
    key = (cfg.embedding_provider, cfg.voyage_api_key, cfg.openai_api_key)
    with _lock:
        if _cached is not None and _cached_key == key:
            return _cached
        provider = cfg.embedding_provider
        if provider == "voyage" and cfg.voyage_api_key:
            emb = VoyageEmbedder(cfg.voyage_api_key)
        elif provider == "openai" and cfg.openai_api_key:
            emb = OpenAIEmbedder(cfg.openai_api_key)
        else:
            if provider not in ("local", ""):
                log.error("Embedding provider %r not usable (missing key?); using local embedder", provider)
            emb = LocalHashEmbedder()
        _cached, _cached_key = emb, key
        return emb


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
