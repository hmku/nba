import json

from fantasy.data.injuries import games_missed_from_return, model_status, parse_cbs, parse_espn


def test_games_missed_from_return():
    assert games_missed_from_return("2026-10-21") == 0
    assert games_missed_from_return("2027-04-30") == 82
    assert 45 <= games_missed_from_return("2027-02-01") <= 52
    assert games_missed_from_return("") is None


def test_model_status():
    assert model_status("Out", "OFS") == "out_for_season"
    assert model_status("Out", "OUT") == "out"
    assert model_status("Day-To-Day", "GTD") == "gtd"


def test_parse_espn(tmp_path):
    payload = {
        "injuries": [
            {
                "displayName": "Golden State Warriors",
                "injuries": [
                    {
                        "status": "Out",
                        "date": "2026-10-16T10:00Z",
                        "shortComment": "Butler could return Jan-Feb.",
                        "details": {
                            "fantasyStatus": {"abbreviation": "OUT"},
                            "side": "Right",
                            "type": "Knee",
                            "returnDate": "2027-01-02",
                        },
                        "athlete": {"displayName": "Jimmy Butler III", "team": {}},
                    }
                ],
            }
        ]
    }
    p = tmp_path / "inj.json"
    p.write_text(json.dumps(payload))
    df = parse_espn(p)
    assert df.loc[0, "player_id"] == "jimmy-butler"
    assert df.loc[0, "injury"] == "Right Knee" and df.loc[0, "return_date"] == "2027-01-02"


def test_parse_cbs(tmp_path):
    row = (
        '<tr class="TableBase-bodyTr"><td><span class="CellPlayerName--short"><a href="x">B. Adebayo'
        '</a></span><span class="CellPlayerName--long"><span><a href="x">Bam Adebayo</a></span></span>'
        "</td><td> C </td><td><span>Fri, Oct 16</span></td><td> Groin </td><td> Game Time Decision "
        "</td></tr>"
    )
    p = tmp_path / "inj.html"
    p.write_text(f"<table>{row}</table>")
    df = parse_cbs(p)
    assert df.loc[0, "name"] == "Bam Adebayo" and df.loc[0, "status"] == "Game Time Decision"
