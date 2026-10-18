"""League-aware season simulator (H2H categories): rosters of every team, matchup schedule, NBA games
per week, availability spells, weekly scoring with a daily lineup cap, standings and playoffs.

See docs/research/season_simulator.md for the model and its priors.
"""

# 0.2.0: playoff reseeding + higher-seed tiebreak from settings; week calendar fitted to settings.playoff_end_date
# 0.3.0: roster completion uses the shared opponent sampler (no-ADP players get the 1.6x sd like draft simulate)
SIM_VERSION = "season-sim-0.3.0"
