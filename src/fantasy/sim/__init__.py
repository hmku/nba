"""League-aware season simulator (H2H categories): rosters of every team, matchup schedule, NBA games
per week, availability spells, weekly scoring with a daily lineup cap, standings and playoffs.

See docs/research/season_simulator.md for the model and docs/research/sim_calibration.md for its fitted parameters.
"""

# 0.2.0: playoff reseeding + higher-seed tiebreak from settings; week calendar fitted to settings.playoff_end_date
# 0.3.0: roster completion uses the shared opponent sampler (no-ADP players get the 1.6x sd like draft simulate)
# 0.4.0: fitted priors (docs/research/sim_calibration.md): season error by mpg tier + per-stat sds, game variance
#        a*m + b*m^2, correlated weekly noise; rest_share/two_spell_prob validated and unchanged
# 0.5.0: per-game variance from the one game-noise model shared with the draft (fantasy.model.noise,
#        noise-var-1.0; same a*m + b*m^2 values); the constant-CV option (SimParams.game_var="cv") is deleted.
#        Drafted rosters change via valuation-0.3.0 / optimizer-0.3.0. Run params record "noise_model".
# 0.6.0: IL replacement on by default.
# 0.7.0: injury spells placed on the fantasy window as a circle (uniform rotation + gap): availability is flat at
#        gp/82 in every week (0.6.0 was U-shaped and 1-12% low on average: min/max placement on the NBA-season axis)
# 0.8.0: roster cap - 13 non-IL players (settings starters + bench) + IL slots: a team's surplus (lowest value, not
#        Out) is released from the day an IL player returns; IL replacement only fills below 13 healthy non-IL;
#        league-file Yahoo Out players start in a spell (`--no-roster-cap` reproduces 0.7.0; il_replacement.md)
# 0.8.1: il_rep_availability fitted 0.85 -> 0.84 (il_replacement.md)
SIM_VERSION = "season-sim-0.8.1"
