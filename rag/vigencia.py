"""Freshness declared by the note: `stale_after` and `updated`, in the OKF §5.5 style.

Freshness is not guessed from the embedding: it is declared in the frontmatter and compared
against the clock. A note with no `stale_after` does not opine (it stays "undated"), because
punishing the absence turns "I don't know" into "it is false".
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo


def parse_fecha(valor: Any, tz: Optional[str] = None) -> Optional[datetime]:
    """ISO 8601 -> datetime in UTC. `None` if the value is not a date.

    A date with no time (`2026-12-31`) is valid until the END of that day in `tz` (the
    system's if none is passed): that is what someone writing an expiry date expects,
    not the midnight that opens it.
    """
    if not isinstance(valor, str) or not valor.strip():
        return None
    zona = ZoneInfo(tz) if tz else datetime.now().astimezone().tzinfo
    texto = valor.strip()
    try:
        if len(texto) == 10:  # YYYY-MM-DD
            d = date.fromisoformat(texto)
            fin = datetime.combine(d, time(23, 59, 59), tzinfo=zona)
            return fin.astimezone(timezone.utc)
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:  # sin offset: se asume la tz del vault
        dt = dt.replace(tzinfo=zona)
    return dt.astimezone(timezone.utc)


def vigencia(
    datos: dict[str, Any],
    ahora: Optional[datetime] = None,
    tz: Optional[str] = None,
) -> dict[str, Any]:
    """Freshness verdict from the already parsed frontmatter.

    Returns `stale_after`/`updated` normalized to ISO UTC, `stale` (None if the note
    declares no date: no opinion) and `days_stale`.
    """
    ahora = ahora or datetime.now(timezone.utc)
    vence = parse_fecha(datos.get("stale_after"), tz=tz)
    actualizado = parse_fecha(datos.get("updated") or datos.get("modified"), tz=tz)
    vencida = None if vence is None else ahora >= vence
    dias = None
    if vencida and vence is not None:
        dias = max(0, (ahora - vence).days)
    return {
        "stale_after": vence.isoformat() if vence else None,
        "updated": actualizado.isoformat() if actualizado else None,
        "stale": vencida,
        "days_stale": dias,
    }


def resumen(vencidas: int, sin_fecha: int, total: int) -> str:
    """Short line for `status`: the total is stated along with the detail."""
    partes = [f"{vencidas} stale", f"{sin_fecha} undated"]
    return f"{total} notes ({', '.join(partes)})"


def fecha_local(iso: Optional[str], tz: Optional[str] = None) -> str:
    """ISO UTC -> `YYYY-MM-DD` in the local zone (or in `tz` if passed).

    It is stored in UTC but shown locally: otherwise a `stale_after: 2026-01-15`
    written in -03 would read as 2026-01-16 and look one day late.
    """
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    return dt.astimezone(ZoneInfo(tz) if tz else None).strftime("%Y-%m-%d")
