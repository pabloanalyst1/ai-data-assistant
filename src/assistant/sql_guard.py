"""SQL guard: every query the LLM writes is validated before it runs, and runs with the least privilege.

Layers (each one alone would block a write; together they also block reading what the assistant should not):

1. **Static checks** on the text, with string literals and comments masked out: exactly one statement, it
   starts with SELECT or WITH, no write / DDL / PRAGMA / ATTACH keywords, no 'now' or CURRENT_DATE (answers
   must be measured from the data's reference date, so they are reproducible).
2. **Compile-time authorization**: SQLite compiles the statement (EXPLAIN, nothing is executed) with an
   authorizer callback that allows only SELECT, reads of the allowlisted tables and a list of safe functions.
   SQLite itself resolves every table, view, CTE and column, so there is no parser to fool: a table hidden in
   a subquery, a CTE or a quoted name is still reported. Syntax errors and unknown columns surface here too.
   The authorizer is not told about columns compared in a `JOIN ... USING`, so the compiled program itself is
   checked as well: every table or index it opens (OpenRead) is mapped back to its table, which must be on
   the allowlist, and a program that opens anything for writing is rejected.
3. **Read-only execution**: the query runs on a connection opened with `mode=ro` and `PRAGMA query_only`,
   with the same authorizer, a time limit (progress handler) and a cap on the rows fetched.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
    "ANALYZE", "TRUNCATE", "GRANT", "REVOKE", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "UPSERT",
)

ALLOWED_FUNCTIONS = frozenset({
    # aggregates
    "count", "sum", "total", "avg", "min", "max", "group_concat", "string_agg",
    # window functions
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead", "first_value",
    "last_value", "nth_value",
    # scalar
    "abs", "round", "coalesce", "ifnull", "nullif", "iif", "if", "lower", "upper", "length", "substr", "substring",
    "trim", "ltrim", "rtrim", "replace", "instr", "printf", "format", "like", "glob", "typeof", "sign", "ceil",
    "ceiling", "floor", "sqrt", "pow", "power", "exp", "ln", "log", "log10", "log2", "mod", "concat", "concat_ws",
    "char", "unicode",
    # dates
    "date", "time", "datetime", "julianday", "strftime", "unixepoch", "timediff",
})

# SQLite authorizer action codes (sqlite3 module constants), for readable error messages
_ACTIONS = {v: k for k, v in vars(sqlite3).items() if k.startswith("SQLITE_") and isinstance(v, int)
            and k not in ("SQLITE_OK", "SQLITE_DENY", "SQLITE_IGNORE")}


class GuardError(ValueError):
    """The query was rejected before running. `reasons` lists every problem found."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class QueryResult:
    sql: str
    data: pd.DataFrame
    truncated: bool
    elapsed_ms: float
    tables: list[str] = field(default_factory=list)


def mask_literals_and_comments(sql: str) -> str:
    """Replace the content of string literals, quoted identifiers and comments with spaces (same length),
    so keyword checks never trigger on text inside them and never miss text outside them."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"`[":
            close = "]" if ch == "[" else ch
            j = i + 1
            while j < n:
                if sql[j] == close:
                    if close != "]" and j + 1 < n and sql[j + 1] == close:  # escaped quote ''
                        j += 2
                        continue
                    break
                j += 1
            out.append(ch + " " * (min(j, n - 1) - i - 1) + (close if j < n else ""))
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


_CTE_NAME = re.compile(r"(?:\bWITH(?:\s+RECURSIVE)?|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^()]*\))?\s+AS\s*"
                       r"(?:NOT\s+)?(?:MATERIALIZED\s+)?\(", re.IGNORECASE)


def cte_names(sql: str) -> set[str]:
    """Names of the common table expressions a query defines (WITH x AS (...), y(a, b) AS (...))."""
    return {m.group(1).lower() for m in _CTE_NAME.finditer(mask_literals_and_comments(sql))}


def strip_sql(sql: str) -> str:
    """Trim whitespace, a ```sql fence and trailing semicolons."""
    sql = sql.strip()
    sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", sql, flags=re.IGNORECASE).strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    return sql


class SQLGuard:
    def __init__(self, db_path: Path | str, allowed_tables: set[str] | frozenset[str], max_rows: int = 500,
                 timeout_s: float = 10.0):
        self.db_path = Path(db_path)
        self.allowed_tables = frozenset(t.lower() for t in allowed_tables)
        self.max_rows = max_rows
        self.timeout_s = timeout_s

    # -- layer 1 -------------------------------------------------------------------------------------
    def static_check(self, sql: str) -> list[str]:
        problems = []
        if not sql:
            return ["The query is empty."]
        masked = mask_literals_and_comments(sql)
        if ";" in masked:
            problems.append("Only one statement is allowed (found ';' between statements).")
        first = re.match(r"\s*([A-Za-z]+)", masked)
        if not first or first.group(1).upper() not in ("SELECT", "WITH"):
            problems.append("Only read queries are allowed: the statement must start with SELECT or WITH.")
        upper = masked.upper()
        for kw in FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{kw}\b", upper):
                problems.append(f"Keyword {kw} is not allowed (read-only access).")
        if re.search(r"\bREPLACE\s+INTO\b", upper):
            problems.append("REPLACE INTO is not allowed (read-only access).")
        if re.search(r"'\s*now\s*'", sql, flags=re.IGNORECASE) or re.search(
                r"\bCURRENT_(DATE|TIME|TIMESTAMP)\b", upper):
            problems.append("Do not use the clock ('now', CURRENT_DATE): relative periods are measured from the "
                            "reference date 2026-10-31 (ref_parameters.as_of_date).")
        return problems

    # -- layer 2 -------------------------------------------------------------------------------------
    def _authorizer(self, denied: list[str], tables: set[str], ctes: frozenset[str] = frozenset()):
        def auth(action, arg1, arg2, db_name, source):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                table = (arg1 or "").lower()
                if table in self.allowed_tables:
                    tables.add(table)
                    return sqlite3.SQLITE_OK
                if table in ctes:  # a recursive CTE reading itself
                    return sqlite3.SQLITE_OK
                denied.append(f"Table {arg1} is not available to the assistant.")
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION:
                if (arg2 or "").lower() in ALLOWED_FUNCTIONS:
                    return sqlite3.SQLITE_OK
                denied.append(f"Function {arg2}() is not allowed.")
                return sqlite3.SQLITE_DENY
            if action == getattr(sqlite3, "SQLITE_RECURSIVE", -1):
                return sqlite3.SQLITE_OK
            denied.append(f"Operation {_ACTIONS.get(action, action)} is not allowed (read-only access).")
            return sqlite3.SQLITE_DENY
        return auth

    @staticmethod
    def _root_pages(conn: sqlite3.Connection) -> dict[int, str]:
        """Root page of every table and index -> the table it belongs to."""
        return {r[0]: r[1].lower() for r in conn.execute("SELECT rootpage, tbl_name FROM sqlite_master WHERE rootpage > 0")}

    def _check_program(self, program: list[tuple], pages: dict[int, str], tables: set[str]) -> list[str]:
        problems = []
        for row in program:
            opcode, p2, p3 = row[1], row[3], row[4]
            if opcode in ("OpenWrite", "Destroy", "Clear", "CreateBtree", "VCreate", "VDestroy"):
                problems.append(f"The query would modify the database ({opcode}).")
            elif opcode in ("OpenRead", "ReopenIdx"):
                table = pages.get(p2) if p3 == 0 else None
                if table in self.allowed_tables:
                    tables.add(table)
                else:
                    problems.append(f"Table {table or 'outside the warehouse'} is not available to the assistant.")
        return problems

    def _query_ctes(self, sql: str, conn: sqlite3.Connection) -> frozenset[str]:
        """CTE names the query defines, except names of real tables (so a CTE can never stand in for one)."""
        real = {r[0].lower() for r in conn.execute("SELECT name FROM sqlite_master")}
        return frozenset(cte_names(sql) - real)

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Warehouse not found: {self.db_path}. Run `python run_pipeline.py` first.")
        conn = sqlite3.connect(f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only = ON")
        return conn

    def validate(self, sql: str) -> tuple[str, list[str]]:
        """Return (clean sql, tables read) or raise GuardError with every problem found."""
        sql = strip_sql(sql or "")
        problems = self.static_check(sql)
        if problems:
            raise GuardError(problems)
        denied: list[str] = []
        tables: set[str] = set()
        conn = self._connect()
        try:
            pages, ctes = self._root_pages(conn), self._query_ctes(sql, conn)
            conn.set_authorizer(self._authorizer(denied, tables, ctes))
            program = conn.execute(f"EXPLAIN {sql}").fetchall()  # compiles the statement; nothing runs
        except sqlite3.Error as exc:
            raise GuardError(list(dict.fromkeys(denied)) or [f"SQLite cannot compile the query: {exc}"]) from None
        finally:
            conn.close()
        denied += self._check_program(program, pages, tables)
        if denied:
            raise GuardError(list(dict.fromkeys(denied)))
        return sql, sorted(tables)

    # -- layer 3 -------------------------------------------------------------------------------------
    def execute(self, sql: str) -> QueryResult:
        """Validate, then run read-only with a time limit and a row cap."""
        sql, tables = self.validate(sql)
        denied: list[str] = []
        conn = self._connect()
        conn.set_authorizer(self._authorizer(denied, set(), self._query_ctes(sql, conn)))
        deadline = time.perf_counter() + self.timeout_s
        conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10_000)
        t0 = time.perf_counter()
        try:
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(self.max_rows + 1)
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise TimeoutError(f"The query took longer than {self.timeout_s:.0f} seconds and was stopped.") from None
            raise
        finally:
            conn.close()
        truncated = len(rows) > self.max_rows
        data = pd.DataFrame(rows[: self.max_rows], columns=columns)
        return QueryResult(sql, data, truncated, (time.perf_counter() - t0) * 1000, tables)
