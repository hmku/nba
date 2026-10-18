import json

import pandas as pd

from fantasy.data.yahoo_public import UNDRAFTED_SLOT, effective_adp, parse, update_manual


def _player(pid, name, elig, adp=None, pct=None, status=None):
    info = [
        {"player_key": f"478.p.{pid}"},
        {"player_id": str(pid)},
        {"name": {"full": name}},
        {"editorial_team_abbr": "DEN"},
        {"display_position": ",".join(elig)},
        {"eligible_positions": [{"position": p} for p in [*elig, "Util"]]},
        [],
    ]
    if status:
        info.append({"status": status, "status_full": "Questionable", "injury_note": "Ankle"})
    da = {"draft_analysis": [{"average_pick": adp or "-"}, {"percent_drafted": pct or "-"}]}
    return {"player": [info, da]}


def _write(tmp_path):
    players = {
        "0": _player(1, "Nikola Jokić", ["C"], "1.4", "1.00"),
        "1": _player(2, "Bobby Portis Jr.", ["PF", "C"], "110.0", "0.10", status="Q"),
        "2": _player(3, "Nobody Drafted", ["SG"]),
        "count": 3,
    }
    page = {"fantasy_content": {"league": [{"league_key": "478.l.101"}, {"players": players}]}}
    p = tmp_path / "raw.json"
    p.write_text(json.dumps([{"start": 0, "url": "x", "data": page}]))
    return p


def test_parse_rows(tmp_path):
    df = parse(_write(tmp_path))
    assert list(df["name"]) == ["Nikola Jokić", "Bobby Portis Jr.", "Nobody Drafted"]
    assert df.loc[0, "positions"] == "C" and df.loc[1, "positions"] == "PF|C"
    assert df.loc[1, "status"] == "Q" and df.loc[1, "injury_note"] == "Ankle"
    assert df.loc[0, "adp"] == 1.4 and pd.isna(df.loc[2, "adp"])


def test_effective_adp():
    assert effective_adp(1.4, 1.0) == 1.4
    assert effective_adp(110.0, 0.1) == round(0.1 * 110 + 0.9 * UNDRAFTED_SLOT, 1)
    assert effective_adp(None, None) == UNDRAFTED_SLOT


def test_update_manual(tmp_path):
    (tmp_path / "positions_overrides.csv").write_text(
        "name,positions,note\nNikola Jokić,C|PF,\nSome Rookie,SF,old guess\n"
    )
    out = update_manual(parse(_write(tmp_path)), "2026-10-18T00:00:00Z", manual_dir=tmp_path)
    assert out["adp_rows"] == 3 and out["position_rows_kept_manual"] == 1
    adp = pd.read_csv(tmp_path / "adp_yahoo_2026_27.csv", comment="#")
    assert list(adp.columns[:4]) == ["name", "adp", "source", "as_of"]
    assert adp["adp"].iloc[2] == UNDRAFTED_SLOT
    pos = pd.read_csv(tmp_path / "positions_overrides.csv", comment="#").fillna("")
    assert pos.set_index("name").loc["Nikola Jokić", "positions"] == "C"
    assert pos.set_index("name").loc["Some Rookie", "source"] == "manual"
