"""Ingestion modules without the network: bbref GitHub mirror (httpx MockTransport) and the nba_api mapper."""

import httpx
import pandas as pd

from fantasy.data import bbref_github, nba_api_ingest
from fantasy.data.provenance import read_provenance


def test_bbref_github_fetch_filters_and_writes_provenance(tmp_path, monkeypatch):
    totals = "season,lg,player,pts\n2021,NBA,Old,1\n2025,NBA,New,2\n2025,ABA,Other,3\n"
    career = "player,from,to\nOld,2010,2021\nNew,2020,2026\n"
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, text=career if "Career" in str(req.url) else totals)

    real_client = httpx.Client
    monkeypatch.setattr(bbref_github.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(bbref_github, "RAW_DIR", tmp_path)
    provs = bbref_github.fetch(min_season=2022)
    assert len(provs) == len(bbref_github.FILES) and all("%20" in u for u in seen)  # spaces escaped
    out = pd.read_csv(tmp_path / bbref_github.SOURCE / "player_totals.csv")
    assert list(out["player"]) == ["New"]  # season >= 2022 and NBA only
    career_out = pd.read_csv(tmp_path / bbref_github.SOURCE / "player_career_info.csv")
    assert list(career_out["player"]) == ["New"]
    prov = read_provenance(tmp_path / bbref_github.SOURCE / "player_totals.csv")
    assert prov is not None and prov["rows"] == 1 and prov["upstream_rows"] == 3 and len(prov["sha256"]) == 64


def test_nba_api_to_bbref_like_maps_columns():
    df = pd.DataFrame([{"PLAYER_NAME": "A B", "PLAYER_ID": 7, "AGE": 25, "TEAM_ABBREVIATION": "BOS", "GP": 70,
                        "MIN": 2100, "FGM": 500, "FGA": 1000, "FG3M": 100, "FG3A": 280, "FTM": 200, "FTA": 250,
                        "REB": 400, "AST": 300, "STL": 70, "BLK": 30, "TOV": 150, "PTS": 1300}])
    out = nba_api_ingest.to_bbref_like(df, 2026)
    row = out.iloc[0]
    assert row["player_id"] == "nba7" and row["season"] == 2026 and row["lg"] == "NBA"
    assert (row["fg"], row["fga"], row["x3p"], row["trb"], row["tov"], row["pts"]) == (500, 1000, 100, 400, 150, 1300)
