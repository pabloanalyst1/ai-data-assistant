"""The evaluation set and its scorer."""
import sqlite3

import pandas as pd
import pytest

from src.assistant.core import DataAssistant
from src.assistant.knowledge import load_knowledge
from src.assistant.llm import MockLLM
from src.assistant.retriever import KnowledgeRetriever
from src.assistant.sql_guard import SQLGuard
from src.evaluation import accuracy, compare_results, evaluate, gold_results, load_questions

QUESTIONS = load_questions()


def test_the_set_has_25_distinct_questions_some_in_spanish():
    assert len(QUESTIONS) == 25 and len({q.id for q in QUESTIONS}) == 25
    assert sum("spanish" in q.tags for q in QUESTIONS) == 5
    assert {q.compare for q in QUESTIONS} == {"set", "first_row", "values"}


def test_eval_questions_are_not_copies_of_the_worked_examples():
    examples = {e.question.lower() for e in load_knowledge().examples}
    assert not examples & {q.question.lower() for q in QUESTIONS}


@pytest.mark.parametrize("q", QUESTIONS, ids=[q.id for q in QUESTIONS])
def test_gold_answers_are_valid_and_pass_the_guard(default_warehouse, q):
    guard = SQLGuard(default_warehouse, load_knowledge().allowed_tables)
    for sql in [q.gold_sql, *q.alt_gold_sql]:
        guard.validate(sql)  # the gold answer itself respects the assistant's rules
    golds = gold_results(default_warehouse, q)
    assert all(len(g) > 0 for g in golds)
    assert compare_results(golds[0], golds[0], q.compare)[0]


def test_known_answers_on_the_default_data(default_warehouse):
    """A few gold answers, pinned: they are quoted in the README."""
    by_id = {q.id: q for q in QUESTIONS}
    assert gold_results(default_warehouse, by_id["q01"])[0].iloc[0, 0] == "Beauty"
    rates = gold_results(default_warehouse, by_id["q02"])[0].set_index("acquisition_channel")["repurchase_rate_pct"]
    assert rates.to_dict() == {"APP": 50.6, "MARKETPLACE": 25.5, "STORE": 38.8, "WEB": 35.4}


@pytest.mark.parametrize("example", load_knowledge().examples, ids=lambda e: e.question[:40])
def test_worked_examples_run_and_pass_the_guard(small_warehouse, example):
    SQLGuard(small_warehouse, load_knowledge().allowed_tables).execute(example.sql)


def test_a_model_that_writes_the_gold_sql_scores_100(default_warehouse, scripted):
    llm = scripted(*[{"sql": q.gold_sql} for q in QUESTIONS])
    assistant = DataAssistant(default_warehouse, llm, explain=False)
    assert accuracy(evaluate(assistant, default_warehouse, QUESTIONS)) == 1.0


def test_offline_baseline_runs_without_a_key(default_warehouse):
    knowledge = load_knowledge()
    retriever = KnowledgeRetriever(knowledge)
    assistant = DataAssistant(default_warehouse, MockLLM(retriever), knowledge, retriever, explain=False)
    results = evaluate(assistant, default_warehouse, QUESTIONS[:5])
    assert len(results) == 5 and all(r.reason for r in results)


# -- the scorer -----------------------------------------------------------------------------------------
GOLD = pd.DataFrame({"channel": ["APP", "WEB"], "rate_pct": [50.6, 35.4]})


@pytest.mark.parametrize("pred,expected", [
    (pd.DataFrame({"rate": [0.354, 0.506], "c": ["Web", "App"]}), True),           # fractions, other order/case
    (pd.DataFrame({"channel": ["APP", "WEB"], "n": [9, 7], "r": [50.61, 35.38]}), True),  # extra column, rounding
    (pd.DataFrame({"channel": ["APP", "WEB"], "r": [35.4, 50.6]}), False),          # values swapped between rows
    (pd.DataFrame({"channel": ["APP", "WEB"], "r": [51.6, 35.4]}), False),          # wrong number
    (pd.DataFrame({"channel": ["APP"], "r": [50.6]}), False),                       # missing row
])
def test_set_comparison(pred, expected):
    assert compare_results(GOLD, pred, "set")[0] is expected


def test_labels_in_another_language_count_if_the_numbers_identify_the_rows():
    gold = pd.DataFrame({"channel_type": ["Offline", "Online"], "aov": [755.83, 828.14]})
    spanish = pd.DataFrame({"tipo": ["En línea", "Tiendas físicas"], "orders": [22898, 32131], "aov": [828.14, 755.83]})
    assert compare_results(gold, spanish, "set") == (True, "ok (labels worded differently)")
    wrong = spanish.assign(aov=[828.14, 700.0])
    assert not compare_results(gold, wrong, "set")[0]                       # a wrong number still fails
    tied = pd.DataFrame({"c": ["A", "B"], "v": [1.0, 1.0]})                 # numbers cannot tell the rows apart
    assert not compare_results(tied, pd.DataFrame({"x": ["Uno", "Dos"], "v": [1.0, 1.0]}), "set")[0]


def test_saved_answers_can_be_rescored_without_the_llm(default_warehouse):
    from src.evaluation import rescore

    q = QUESTIONS[1]
    saved = [{"id": q.id, "sql": q.gold_sql, "reason": "", "attempts": 1, "seconds": 1.0},
             {"id": QUESTIONS[2].id, "sql": None, "reason": "Claude API error", "attempts": 0, "seconds": 0.1}]
    results = rescore(default_warehouse, saved, QUESTIONS)
    assert [r.correct for r in results] == [True, False]


def test_first_row_and_values_comparisons():
    assert compare_results(pd.DataFrame({"m": ["2025-12"]}), pd.DataFrame({"month": ["2025-12-01"], "s": [9.0]}),
                           "first_row")[0]
    assert not compare_results(pd.DataFrame({"m": ["Beauty"]}), pd.DataFrame({"c": ["Fashion", "Beauty"]}),
                               "first_row")[0]
    gold = pd.DataFrame({"a": [43248144.23], "b": [34970289.96]})
    long = pd.DataFrame({"year": [2025, 2026], "net_sales": [34970289.9, 43248144.2]})
    assert compare_results(gold, long, "values")[0]
    assert not compare_results(gold, long.head(1), "values")[0]


def test_scorer_handles_failed_answers():
    assert compare_results(GOLD, None, "set") == (False, "no result")


def test_gold_results_use_sqlite(default_warehouse):
    with sqlite3.connect(default_warehouse) as conn:
        assert conn.execute("SELECT as_of_date FROM ref_parameters").fetchone()[0] == "2026-10-31"
