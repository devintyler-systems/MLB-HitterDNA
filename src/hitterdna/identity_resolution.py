"""Deterministic, non-guessing player-name identity resolution."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Literal, Mapping
import unicodedata


ResolutionStatus = Literal["resolved", "unresolved", "ambiguous"]


@dataclass(frozen=True)
class IdentityResolution:
    status: ResolutionStatus
    player_mlbam_id: int | None
    player_name: str | None
    candidate_mlbam_ids: tuple[int, ...]


def resolve_player_name(
    player_name: str, players: Iterable[Mapping[str, Any]]
) -> IdentityResolution:
    """Resolve an exact normalized name only when it identifies one player.

    This function deliberately has no fuzzy matching or fallback IDs. Multiple
    matching records yield ``ambiguous``; no match (or no usable ID) yields
    ``unresolved``.
    """

    requested_name = normalize_player_name(player_name)
    if not requested_name:
        return IdentityResolution("unresolved", None, None, ())

    matches = [
        player
        for player in players
        if normalize_player_name(_player_name(player)) == requested_name
    ]
    candidate_ids = tuple(
        player_id
        for player in matches
        if (player_id := _positive_integer(player.get("id"))) is not None
    )

    if len(matches) != 1 or len(candidate_ids) != 1:
        status: ResolutionStatus = "ambiguous" if len(matches) > 1 else "unresolved"
        return IdentityResolution(status, None, None, candidate_ids)

    return IdentityResolution(
        "resolved",
        candidate_ids[0],
        _player_name(matches[0]),
        candidate_ids,
    )


def normalize_player_name(name: str) -> str:
    """Normalize only presentation differences, never perform fuzzy matching."""

    normalized = unicodedata.normalize("NFKC", name).casefold().strip()
    return re.sub(r"[\W_]+", " ", normalized).strip()


def _player_name(player: Mapping[str, Any]) -> str:
    full_name = player.get("fullName")
    if isinstance(full_name, str):
        return full_name
    first_name = player.get("firstName")
    last_name = player.get("lastName")
    if isinstance(first_name, str) and isinstance(last_name, str):
        return f"{first_name} {last_name}"
    return ""


def _positive_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None
