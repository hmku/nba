"""Value-network draft search (AlphaGo-style): simulated leagues -> learned season-outcome model ->
rollout search at draft time. See docs/research/value_search.md.

Modules: `policies` (draft engine + drafting policies), `features` (permutation-invariant league
featurization), `model` (value model + artifact), `search` (draft-time search), `datagen` (training
data), `truth` (market-is-right projection proxy), `validate` (paired policy validation).
"""

VALUE_SEARCH_VERSION = "value-search-0.1.0"
