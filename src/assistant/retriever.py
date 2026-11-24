"""Retrieval (the R in RAG): pick the glossary definitions and worked examples relevant to a question.

The knowledge base is small (25 definitions, 16 examples), so a keyword ranker (Okapi BM25) is enough, and it is
deterministic, testable offline and needs no embedding API. Glossary entries carry Spanish and English aliases,
so "tasa de recompra" finds the repurchase-rate definition. The `Retriever` interface is all the assistant
depends on: a vector store (e.g. LangChain + embeddings) can replace it without touching anything else.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from src.assistant.knowledge import Example, GlossaryEntry, Knowledge

STOPWORDS = frozenset("""
a an and are as at be by did do does for from had has have how i in is it its many me much of on or our show
tell than that the their them there these this to us was we were what when where which who why will with you
your give list each per vs versus
al como con cual cuál cuales cuáles cuando cuanto cuánto cuantos cuántos de del el en es esta este fue fueron
han hay la las lo los mas más me mi mis nos nuestra nuestro nuestros o para por que qué se sobre son su sus
tiene tienen un una uno y muestra muestrame dame cada frente
""".split())


def normalize(text: str) -> str:
    """Lower case without accents: 'Categoría' -> 'categoria'."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _stem(word: str) -> str:
    """Very light plural stripping for English and Spanish ('categories' -> 'category', 'ventas' -> 'venta')."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("es") and word[-3] in "lnrd":  # clientes, canales, ordenes
        return word[:-2] if word[-3] in "lnr" else word[:-1]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Words without stopwords or numbers (years in the question would match every definition that has a date)."""
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", normalize(text)) if w not in STOPWORDS and not w.isdigit()]


class BM25:
    def __init__(self, documents: list[str], k1: float = 1.4, b: float = 0.6):
        self.docs = [tokenize(d) for d in documents]
        self.k1, self.b = k1, b
        self.avg_len = sum(map(len, self.docs)) / max(1, len(self.docs))
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for tf, doc in zip(self.tf, self.docs):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(doc) / self.avg_len))
            out.append(s)
        return out

    def top(self, query: str, k: int) -> list[tuple[int, float]]:
        ranked = sorted(enumerate(self.scores(query)), key=lambda x: (-x[1], x[0]))
        return [(i, s) for i, s in ranked[:k] if s > 0]


@dataclass
class RetrievedContext:
    glossary: list[GlossaryEntry]
    examples: list[Example]


class Retriever(Protocol):
    def retrieve(self, question: str) -> RetrievedContext: ...


class KnowledgeRetriever:
    """BM25 over the glossary (term, aliases, definition) and over the example questions."""

    def __init__(self, knowledge: Knowledge, k_glossary: int = 5, k_examples: int = 3):
        self.knowledge = knowledge
        self.k_glossary, self.k_examples = k_glossary, k_examples
        self._always = [e for e in knowledge.glossary if e.always]
        self._searchable = [e for e in knowledge.glossary if not e.always]
        self._glossary_index = BM25([e.search_text() for e in self._searchable])
        self._example_index = BM25([e.question for e in knowledge.examples])

    def retrieve(self, question: str) -> RetrievedContext:
        hits = [self._searchable[i] for i, _ in self._glossary_index.top(question, self.k_glossary)]
        examples = [self.knowledge.examples[i] for i, _ in self._example_index.top(question, self.k_examples)]
        return RetrievedContext(glossary=self._always + hits, examples=examples)

    def closest_example(self, question: str) -> Example | None:
        top = self._example_index.top(question, 1)
        return self.knowledge.examples[top[0][0]] if top else None
