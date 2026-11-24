"""Retail data warehouse pipeline for the AI data assistant (Casa Ceiba, a fictional omnichannel retailer).

Steps:
  1/5 EXTRACT    - generate the synthetic POS, e-commerce, CRM and catalog extracts (messy, like the real thing)
  2/5 LOAD       - save the raw files; load them into raw_* / ref_* tables in the SQLite warehouse
  3/5 TRANSFORM  - SQL models: staging -> dimensions -> facts -> marts
  4/5 QUALITY    - data quality checks (the pipeline stops if any fails)
  5/5 EXPORT     - marts to CSV for the BI tool

Usage:
  python run_pipeline.py              # default: Jan 2024 - Oct 2026, seed 42
  python run_pipeline.py --seed 7
"""
from __future__ import annotations

import argparse
import logging
import time

from src.config import PipelineConfig
from src.export import export_marts
from src.generate_data import generate_all
from src.load import load_raw, save_raw_files
from src.quality import run_quality_checks
from src.transform import run_sql_models

log = logging.getLogger("pipeline")


def run(cfg: PipelineConfig, export: bool = True) -> None:
    t0 = time.perf_counter()
    log.info("1/5 EXTRACT   - simulating Casa Ceiba %s to %s (seed %s)", cfg.start_date, cfg.end_date, cfg.seed)
    raw, ref = generate_all(seed=cfg.seed, start=cfg.start_date, end=cfg.end_date)
    log.info("              %s POS ticket lines, %s e-commerce orders, %s CRM accounts",
             f"{len(raw['pos_ticket_lines']):,}", f"{len(raw['ecom_orders']):,}", f"{len(raw['crm_customers']):,}")

    log.info("2/5 LOAD      - raw files and raw_* tables")
    save_raw_files(raw, cfg.raw_dir)
    load_raw(raw, ref, cfg.db_path, cfg.start_date, cfg.end_date)

    log.info("3/5 TRANSFORM - SQL models")
    run_sql_models(cfg.db_path, cfg.sql_dir)

    log.info("4/5 QUALITY   - data quality checks")
    run_quality_checks(cfg.db_path)

    if export:
        log.info("5/5 EXPORT    - marts to CSV")
        export_marts(cfg.db_path, cfg.export_dir)

    log.info("Done in %.1f s. Warehouse: %s", time.perf_counter() - t0, cfg.db_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42, help="random seed (same seed = same data)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    run(PipelineConfig(seed=args.seed))


if __name__ == "__main__":
    main()
