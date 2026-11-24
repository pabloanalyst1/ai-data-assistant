"""The assistant's knowledge: table documentation, business glossary and worked examples (YAML files in knowledge/)."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from src.config import ROOT

KNOWLEDGE_DIR = ROOT / "knowledge"


@dataclass(frozen=True)
class TableDoc:
    name: str
    description: str
    columns: dict[str, str]


@dataclass(frozen=True)
class GlossaryEntry:
    id: str
    term: str
    definition: str
    aliases: tuple[str, ...] = ()
    sql: str = ""
    always: bool = False

    def search_text(self) -> str:
        return " ".join([self.term, self.term, " ".join(self.aliases), " ".join(self.aliases), self.definition])

    def to_prompt(self) -> str:
        text = f"- {self.term}: {self.definition.strip()}"
        return text + (f"\n  SQL: {self.sql.strip()}" if self.sql else "")


@dataclass(frozen=True)
class Example:
    question: str
    sql: str


@dataclass(frozen=True)
class Knowledge:
    tables: dict[str, TableDoc]
    glossary: tuple[GlossaryEntry, ...]
    examples: tuple[Example, ...] = field(default=())

    @property
    def allowed_tables(self) -> frozenset[str]:
        return frozenset(self.tables)

    def schema_prompt(self) -> str:
        """Compact schema for the prompt: one block per table with every column and its meaning."""
        blocks = []
        for t in self.tables.values():
            cols = "\n".join(f"  - {c}: {d}" for c, d in t.columns.items())
            blocks.append(f"{t.name}: {t.description.strip()}\n{cols}")
        return "\n\n".join(blocks)


def _read(path: Path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=4)
def load_knowledge(directory: Path = KNOWLEDGE_DIR) -> Knowledge:
    tables = {name: TableDoc(name, spec["description"], dict(spec["columns"]))
              for name, spec in _read(directory / "schema.yaml").items()}
    glossary = tuple(GlossaryEntry(id=e["id"], term=e["term"], definition=e["definition"],
                                   aliases=tuple(e.get("aliases", [])), sql=e.get("sql", ""),
                                   always=bool(e.get("always", False)))
                     for e in _read(directory / "glossary.yaml"))
    examples = tuple(Example(e["question"], e["sql"].strip()) for e in _read(directory / "examples.yaml"))
    return Knowledge(tables, glossary, examples)
