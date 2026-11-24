"""Smoke tests: the Streamlit app renders and answers in demo mode (runs where streamlit is installed)."""
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


@pytest.fixture
def demo_env(monkeypatch, small_warehouse):
    for var in ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ASSISTANT_DB_PATH", str(small_warehouse))


def test_chat_page_renders_in_demo_mode(demo_env):
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception, at.exception
    assert "Demo mode" in at.info[0].value
    assert len(at.sidebar.button) >= 6


def test_asking_a_question_shows_the_answer(demo_env):
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    at.chat_input[0].set_value("What were net sales by quarter?").run()
    assert not at.exception, at.exception
    assert len(at.dataframe) == 1
    assert any("SELECT" in c.value for c in at.code)


def test_example_button_asks_the_question(demo_env):
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    at.sidebar.button[0].click().run()
    assert not at.exception, at.exception
    assert len(at.dataframe) == 1


def test_overview_page_renders(demo_env):
    testing = pytest.importorskip("streamlit.testing.v1")
    at = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    at.sidebar.radio[0].set_value("Business overview").run()
    assert not at.exception, at.exception
    assert len(at.metric) == 4 and at.metric[0].label == "Net sales (GTQ)"


def test_missing_warehouse_is_explained(monkeypatch, tmp_path):
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("ASSISTANT_DB_PATH", str(tmp_path / "missing.db"))
    at = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert "run_pipeline.py" in at.error[0].value


def test_app_builds_the_warehouse_on_first_start(tmp_path, monkeypatch):
    """No warehouse and no ASSISTANT_DB_PATH (a fresh Streamlit Cloud deploy): the app runs the pipeline itself."""
    testing = pytest.importorskip("streamlit.testing.v1")
    import src.config as config

    monkeypatch.delenv("ASSISTANT_DB_PATH", raising=False)
    for var in ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    original = config.PipelineConfig.__init__

    def small_config(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.raw_dir, self.db_path = tmp_path / "raw", tmp_path / "warehouse.db"
        self.start_date, self.end_date = "2025-09-01", "2025-12-31"

    monkeypatch.setattr(config.PipelineConfig, "__init__", small_config)
    at = testing.AppTest.from_file(str(APP), default_timeout=180).run()
    assert not at.exception, at.exception
    assert (tmp_path / "warehouse.db").exists()
