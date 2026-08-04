"""Named SQL constants and the runner that serialises analytics results.

Every query the challenge asks for lives in :mod:`src.analytics.queries` as a
named string constant with inline commentary. This ``__init__`` re-exports the
runner so the pipeline can ``from src.analytics import runner``.

Defect codes owned: none directly — queries *consume* the cleaning decisions
(TX-03 discount preservation, TX-06 guest exclusion, TX-10 signed returns)
rather than performing them.
"""
