import json
from pathlib import Path
from typing import Any

from hitterdna.identity_resolution import resolve_player_name


FIXTURES = Path(__file__).parent / "fixtures"


def load_players() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "players.json").read_text())


def test_unique_player_name_resolves_to_authoritative_mlbam_id() -> None:
    resolution = resolve_player_name("juan soto", load_players())

    assert resolution.status == "resolved"
    assert resolution.player_mlbam_id == 665742
    assert resolution.player_name == "Juan Soto"


def test_ambiguous_player_name_never_returns_an_id() -> None:
    resolution = resolve_player_name("Chris Taylor", load_players())

    assert resolution.status == "ambiguous"
    assert resolution.player_mlbam_id is None
    assert resolution.candidate_mlbam_ids == (700001, 700002)


def test_unresolved_player_name_never_returns_an_id() -> None:
    resolution = resolve_player_name("Not A Player", load_players())

    assert resolution.status == "unresolved"
    assert resolution.player_mlbam_id is None
    assert resolution.candidate_mlbam_ids == ()
