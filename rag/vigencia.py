"""Vigencia declarada por la nota: `stale_after` y `updated`, al estilo OKF §5.5.

La frescura no se adivina con el embedding: se declara en el frontmatter y se compara
contra el reloj. Una nota sin `stale_after` no opina (queda "sin fecha"), porque
castigar la ausencia convierte "no sé" en "es falso".
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo


def parse_fecha(valor: Any, tz: Optional[str] = None) -> Optional[datetime]:
    """ISO 8601 -> datetime en UTC. `None` si el valor no es una fecha.

    Una fecha sin hora (`2026-12-31`) vale hasta el FINAL de ese día en `tz` (la del
    sistema si no se pasa una): es lo que espera quien escribe una fecha de vencimiento,
    no la medianoche que la abre.
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
    """Veredicto de vigencia a partir del frontmatter ya parseado.

    Devuelve `stale_after`/`updated` normalizados en ISO UTC, `vencida` (None si la nota
    no declara fecha: no se opina) y `dias_vencida`.
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
        "vencida": vencida,
        "dias_vencida": dias,
    }


def resumen(vencidas: int, sin_fecha: int, total: int) -> str:
    """Línea corta para `status`: se dice el total además del detalle."""
    partes = [f"{vencidas} vencidas", f"{sin_fecha} sin fecha"]
    return f"{total} notas ({', '.join(partes)})"


def fecha_local(iso: Optional[str], tz: Optional[str] = None) -> str:
    """ISO UTC -> `YYYY-MM-DD` en la zona local (o en `tz` si se pasa).

    Se guarda en UTC, pero se muestra en local: si no, un `stale_after: 2026-01-15`
    escrito en -03 se leería como 2026-01-16 y parecería un día de más.
    """
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    return dt.astimezone(ZoneInfo(tz) if tz else None).strftime("%Y-%m-%d")
