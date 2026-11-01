"""In-season tools: position-aware daily lineups, streaming ranker, trade evaluation.

Analysis only: nothing here touches Yahoo. Adds/drops are executed by a live run (no approval since 2026-10-31); trades need the user's approval
(CLAUDE.md safety rules). Research note: docs/research/week1_and_trades_2026-10-20.md.
"""

INSEASON_VERSION = "inseason-0.3.1"  # 0.3.1: market set follows the in-season projections
