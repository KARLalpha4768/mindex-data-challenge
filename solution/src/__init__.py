"""Mindex data-engineering challenge: a defect-aware ETL pipeline.

Layering (each layer depends only on the ones above it, so there are no cycles):

    config      -- constants: paths, AS_OF_DATE, thresholds
    defects     -- the 17-entry defect registry (expected counts, decisions, why)
    io_utils    -- string-faithful CSV reads, atomic JSON writes, tag scanning
    audit       -- the decision ledger and the completeness proof
    profiling   -- pre-cleaning measurement of the raw files
    cleaning    -- the detect / decide / record layer, one module per dataset
    warehouse   -- SQLite star schema DDL and transactional loader
    analytics   -- named SQL + JSON serialisation of the business metrics
    pipeline    -- orchestration and CLI entry point

Run it with ``python -m src.pipeline`` from the repository root.
"""

__version__ = "1.0.0"
