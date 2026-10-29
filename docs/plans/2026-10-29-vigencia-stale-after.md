# Vigencia de las notas (`stale_after`) — plan de implementación

> **Para Hermes:** ejecutar tarea por tarea con `subagent-driven-development` o directo, con TDD y commit por tarea.

**Goal:** que el ranking sepa si una nota está vencida y lo use, en vez de tratar "lo más parecido" como "lo más cierto".

**Arquitectura:** la vigencia no se adivina, se declara. Cada nota puede traer `stale_after` (fecha absoluta, igual que OKF §5.5) y `updated` en el frontmatter; el índice los guarda normalizados en `notes`, `search()` aplica un factor de frescura y cada resultado dice si está vencido. Sin frontmatter no hay juicio: la nota queda "sin fecha" y no se penaliza, porque castigar por ausencia inventa información.

**Stack:** Python 3.11+, sqlite3, unittest. Sin dependencias nuevas (el frontmatter ya se parsea a mano en `rag/vault.py`).

---

## Decisiones que el plan asume (cambialas antes de empezar y se propagan solas)

| Decisión | Valor | Por qué |
|---|---|---|
| Campo | `stale_after` en frontmatter | Compatible con OKF v0.2 §5.5: misma semántica, mismo nombre |
| Semántica | vencida si `now >= stale_after` | La spec lo define así; comparación de instantes, sin TTL relativo |
| Fecha sin hora (`2026-12-31`) | vale hasta el final de ese día, hora local | Es el uso real en un vault; un instante a las 00:00 vence el día que el autor quiso cubrir |
| Nota sin `stale_after` | `NULL` → no se penaliza ni se filtra | Castigar la ausencia convierte "no sé" en "es falso" |
| Penalización por defecto | `×0.75` (configurable) | Un vencido puede seguir siendo la única respuesta; bajarlo del podio ya cambia la decisión |
| Filtro duro | sólo con `--fresh-only` | Borrar los vencidos devuelve "no encontrado", que es la misma mentira al revés |
| `updated` | opcional, informativo | No entra al ranking: la frescura la manda `stale_after`, explícita |

---

### Task 1: Parser de fechas del frontmatter

**Objective:** convertir el string del frontmatter en un instante UTC, o `None` si no es una fecha válida.

**Files:**
- Create: `rag/vigencia.py`
- Test: `tests/test_vigencia.py`

**Step 1: test que falla**

```python
# tests/test_vigencia.py
import unittest
from datetime import datetime, timezone

from rag.vigencia import parse_fecha


class Parser(unittest.TestCase):
    def test_fecha_sin_hora_vence_al_final_del_dia_local(self):
        # 2026-12-31 en -03:00 => 2027-01-01T02:59:59Z
        self.assertEqual(parse_fecha("2026-12-31", tz="America/Argentina/Buenos_Aires"),
                         datetime(2027, 1, 1, 2, 59, 59, tzinfo=timezone.utc))

    def test_datetime_con_z(self):
        self.assertEqual(parse_fecha("2026-12-31T10:00:00Z"),
                         datetime(2026, 12, 31, 10, 0, tzinfo=timezone.utc))

    def test_datetime_con_offset(self):
        self.assertEqual(parse_fecha("2026-12-31T10:00:00-03:00"),
                         datetime(2026, 12, 31, 13, 0, tzinfo=timezone.utc))

    def test_basura_devuelve_none(self):
        for malo in ("ayer", "31/12/2026", "", "2026-13-45"):
            self.assertIsNone(parse_fecha(malo), malo)
```

**Step 2: correr y ver el fallo**

```bash
python -m unittest tests.test_vigencia -v
```
Expected: `ModuleNotFoundError: No module named 'rag.vigencia'`

**Step 3: implementación mínima**

```python
# rag/vigencia.py
"""Vigencia declarada por la nota: `stale_after` y `updated`, al estilo OKF §5.5."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo


def parse_fecha(valor: Any, tz: Optional[str] = None) -> Optional[datetime]:
    """ISO 8601 -> datetime con tz en UTC. `None` si el valor no es una fecha.

    Una fecha sin hora (`2026-12-31`) se interpreta como el FINAL de ese día en `tz`
    (la del sistema si no se pasa una): es lo que espera quien escribe la fecha de
    vencimiento, no la medianoche que la abre.
    """
    if not isinstance(valor, str) or not valor.strip():
        return None
    zona = ZoneInfo(tz) if tz else datetime.now().astimezone().tzinfo
    texto = valor.strip()
    try:
        if len(texto) == 10:                      # YYYY-MM-DD
            d = date.fromisoformat(texto)
            fin = datetime.combine(d, time(23, 59, 59), tzinfo=zona)
            return fin.astimezone(timezone.utc)
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:                          # sin offset: se asume la tz del vault
        dt = dt.replace(tzinfo=zona)
    return dt.astimezone(timezone.utc)
```

**Step 4: verde**

```bash
python -m unittest tests.test_vigencia -v
```
Expected: `4 tests, OK`

**Step 5: commit**

```bash
git add rag/vigencia.py tests/test_vigencia.py
git commit -m "feat(vigencia): parser de stale_after/updated compatible con OKF"
```

---

### Task 2: Veredicto de vigencia por nota

**Objective:** dada la data del frontmatter y el "ahora", devolver `stale_after`, `updated` y si está vencida.

**Files:**
- Modify: `rag/vigencia.py`
- Test: `tests/test_vigencia.py`

**Step 1: test**

```python
def test_veredicto(self):
    ahora = datetime(2026, 11, 1, tzinfo=timezone.utc)
    v = vigencia({"stale_after": "2026-10-31"}, ahora=ahora, tz="UTC")
    self.assertTrue(v["vencida"])
    self.assertEqual(v["dias_vencida"], 1)

    v = vigencia({"stale_after": "2026-12-31"}, ahora=ahora, tz="UTC")
    self.assertFalse(v["vencida"])

    v = vigencia({}, ahora=ahora)
    self.assertIsNone(v["vencida"])       # sin fecha: no se opina
    self.assertIsNone(v["stale_after"])
```

**Step 2-4:** correr, implementar, correr.

```python
def vigencia(datos: dict[str, Any], ahora: Optional[datetime] = None,
             tz: str = "UTC") -> dict[str, Any]:
    ahora = ahora or datetime.now(timezone.utc)
    vence = parse_fecha(datos.get("stale_after"), tz=tz)
    actualizado = parse_fecha(datos.get("updated") or datos.get("modified"), tz=tz)
    vencida = None if vence is None else ahora >= vence
    dias = None
    if vencida:
        dias = max(0, (ahora - vence).days)
    return {
        "stale_after": vence.isoformat() if vence else None,
        "updated": actualizado.isoformat() if actualizado else None,
        "vencida": vencida,
        "dias_vencida": dias,
    }
```

**Step 5: commit** — `feat(vigencia): veredicto por nota`

---

### Task 3: Esquema y migración

**Objective:** que un índice existente gane las columnas sin romperse y que la próxima corrida las llene sola.

**Files:**
- Modify: `rag/index.py` (tabla `notes` ~línea 45; `_ensure_columns` ~línea 174; chequeo de rebuild ~línea 529)
- Test: `tests/test_build.py`

**Step 1: test de migración**

```python
def test_migracion_agrega_vigencia(self):
    # DB con el esquema viejo: sin stale_after ni updated
    with sqlite3.connect(self.db) as c:
        c.execute("CREATE TABLE notes (path TEXT PRIMARY KEY, mtime REAL, size INTEGER,"
                  " hash TEXT, n_chunks INTEGER, indexed_at REAL)")
        c.execute("INSERT INTO notes VALUES ('a.md', 0, 1, 'h', 1, 0)")
    index.build(force=False)                    # conecta => migra
    cols = {r[1] for r in index.conn().execute("PRAGMA table_info(notes)")}
    self.assertIn("stale_after", cols)
    self.assertIn("updated", cols)
```

**Step 2-4:** implementar y verificar.

```python
# en CREATE TABLE notes, después de indexed_at:
    stale_after TEXT,      -- ISO 8601 UTC, del frontmatter; NULL = sin fecha declarada
    updated     TEXT       -- ISO 8601 UTC, "cuándo se revisó", informativo

# en _ensure_columns -> expected["notes"]:
    ("stale_after", "TEXT"), ("updated", "TEXT"),

# en el chequeo de rebuild (línea ~529, junto a chunker/modelo), para que un índice viejo
# se rellene una sola vez sin que el usuario tenga que saber que existe:
    or _meta(c, "vigencia") != "1"
# y al final de build(), en el bloque de _set_meta (línea ~693), junto a "chunker"/"modelo":
    _set_meta(c, "vigencia", "1")
```

**Step 5:** `python -m unittest tests.test_build -v` → OK. Commit `feat(index): columnas de vigencia + rebuild único`.

---

### Task 4: Guardar la vigencia al indexar

**Objective:** cada fila de `notes` lleva su `stale_after`/`updated` normalizados.

**Files:**
- Modify: `rag/index.py` (~línea 647, el `INSERT OR REPLACE INTO notes`)
- Test: `tests/test_build.py`

**Step 1: test**

```python
def test_guardar_vigencia(self):
    (self.vault / "a.md").write_text(
        "---\nstale_after: 2026-12-31\nupdated: 2026-10-01\n---\n# A\n\ntexto\n", encoding="utf-8")
    index.build(force=True)
    fila = index.conn().execute(
        "SELECT stale_after, updated FROM notes WHERE path='a.md'").fetchone()
    self.assertTrue(fila[0].startswith("2027-01-01T02:59:59"))
    self.assertTrue(fila[1].startswith("2026-10-01"))
```

**Step 3:** en el loop de `build()` la variable con el contenido de la nota ya existe y se llama **`body`** (es la que alimenta el hash y los tags). El insert está en la línea ~647:

```python
v = vigencia(frontmatter(body))
c.execute("INSERT OR REPLACE INTO notes (path, mtime, size, hash, n_chunks, indexed_at,"
          " stale_after, updated) VALUES (?,?,?,?,?,?,?,?)",
          (path, mt, len(body), chash, len(chunks), time.time(), v["stale_after"], v["updated"]))
```

`frontmatter` no existe todavía con ese nombre: `rag/vault.py:177` define `_frontmatter` (parser a mano, sin PyYAML). Hay que exponerlo público:

```python
# rag/vault.py, junto a la definición
def frontmatter(texto: str) -> dict[str, Any]:
    """Frontmatter como dict. Alias público de `_frontmatter` para otros módulos."""
    return _frontmatter(texto)
```

y en `rag/index.py` la importación ya existente se extiende (no hay import circular: `vault.py` sólo importa `config`):

```python
from .vault import dentro_del_vault, frontmatter
```

**Step 4-5:** test verde, `reindex` real y `SELECT` de control. Commit.

---

### Task 5: Penalización en el ranking

**Objective:** que un vencido pierda contra un vigente cuando compiten por la misma consulta, sin desaparecer.

**Files:**
- Modify: `rag/index.py` (`search()` ~línea 928; carga de `stale_map` antes del loop)
- Modify: `rag/config.py` (`STALE_PENALTY = float(os.getenv("OBS_RAG_STALE_PENALTY", "0.75"))`)
- Test: `tests/test_search.py`

**Step 1: test**

```python
def test_vencida_pierde_contra_vigente(self):
    # dos notas sobre lo mismo; la vieja dice lo mismo pero está vencida
    results = index.search("puerto del servicio", k=5)
    self.assertEqual(results[0]["path"], "nueva.md")
    vieja = next(r for r in results if r["path"] == "vieja.md")
    self.assertTrue(vieja["vencida"])
    self.assertGreater(vieja["score"], 0)     # penalizada, no borrada
```

**Step 3:** antes del loop de candidatos:

```python
stale = dict(c.execute("SELECT path, stale_after FROM notes").fetchall())
ahora = datetime.now(timezone.utc)
```

y dentro del loop, después de los boosts:

```python
vence = stale.get(path)
if vence and ahora >= datetime.fromisoformat(vence):
    score *= config.STALE_PENALTY     # 0.75 por defecto
```

y en el dict de salida: `"vencida": bool(...)`, `"stale_after": vence`.

**Por qué no se pierden esos campos:** `_con_texto()` (línea ~757) sólo hace `pop` de los internos (`_id`, `_cob`, `_nota`). Cualquier clave sin guión bajo sobrevive hasta el resultado final, así que `vencida` y `stale_after` llegan al CLI y al plugin sin tocar esa función.

**Step 4:** `python -m unittest tests.test_search -v`. **Step 5:** commit.

**Cuidado:** re-correr `bench/bench_search.py` y `bench/gate.sh`. Si el top-5 baja, el factor está demasiado agresivo para este corpus y hay que subirlo (medido, no elegido a ojo).

---

### Task 6: `--fresh-only`

**Objective:** poder pedir sólo notas vigentes cuando la respuesta tiene que ser cierta hoy.

**Files:**
- Modify: `rag/index.py` (`search(..., fresh_only: bool = False)`), `rag/obsn.py` (`rag` y `search`)
- Test: `tests/test_search.py`

**Step 1: test**

```python
def test_fresh_only_excluye_vencidas(self):
    paths = [r["path"] for r in index.search("puerto", k=5, fresh_only=True)]
    self.assertNotIn("vieja.md", paths)
```

**Step 3:**

```python
if fresh_only and vence and ahora >= datetime.fromisoformat(vence):
    continue
```

CLI: `obsn rag "q" --fresh-only`. Commit.

---

### Task 7: Contadores en `status`

**Objective:** ver el estado de vigencia del vault en una línea.

**Files:**
- Modify: `rag/obsn.py` (`cmd_status`, ~línea 135)
- Test: `tests/test_build.py` (el test de status ya existe: extenderlo)

**Step 1: test** — crear 3 notas (una vencida, una al día, una sin fecha) y exigir `vencidas=1 sin_fecha=1 por_vencer_7d=0`.

**Step 3:**

```sql
SELECT
  SUM(CASE WHEN stale_after IS NOT NULL AND stale_after <= :ahora THEN 1 ELSE 0 END) AS vencidas,
  SUM(CASE WHEN stale_after IS NULL THEN 1 ELSE 0 END) AS sin_fecha,
  SUM(CASE WHEN stale_after IS NOT NULL AND stale_after > :ahora
            AND stale_after <= :en7 THEN 1 ELSE 0 END) AS por_vencer_7d
FROM notes
```

Se agregan a la salida humana y al `--json`. Commit.

---

### Task 8: `obsn stale`

**Objective:** listar qué está vencido y hace cuánto, para poder arreglarlo.

**Files:**
- Create: `rag/stale.py`
- Modify: `rag/obsn.py` (subcomando + ayuda), `tests/e2e_cli.py`
- Test: `tests/test_vigencia.py`

**Salida esperada:**

```
$ obsn stale
2026-08-14  hace 76 días  02-hermes-docs/infra/dns.md
2026-09-30  hace 29 días  01-Technical/vpn.md

2 vencidas de 203 notas (12 sin fecha)
```

Flags: `--days N` (sólo las que vencieron hace más de N), `--json`. Commit.

---

### Task 9: `meta` y `read` muestran la vigencia

**Objective:** que al abrir una nota se vea si está vencida, sin consultar el índice a mano.

**Files:**
- Modify: `rag/vault.py` (`get_note_metadata`), `rag/obsn.py` (`cmd_meta`)
- Test: `tests/test_vault.py`

Salida nueva en `meta`:

```
stale_after : 2026-09-30T03:59:59+00:00  (VENCIDA hace 29 días)
updated     : 2026-06-01T00:00:00+00:00
```

Commit.

---

### Task 10: Plugin (`vault_search`, `vault_status`)

**Objective:** que el agente vea la vigencia sin tener que preguntar dos veces.

**Files:**
- Modify: `plugins/obsidian-rag/tools.py`, `plugins/obsidian-rag/schemas.py`
- Test: `tests/e2e_plugin.py`

- `vault_search`: cada hit suma `"vencida": true|false|null` y `"stale_after"`; nuevo parámetro `fresh_only` (en los dos modos); la respuesta agrega `"vencidas": N`.
- `vault_status`: `vencidas`, `sin_fecha`.
- Descripciones en `schemas.py`: decir en una línea qué significa `null` (sin fecha declarada ≠ vigente).
- E2E: nota vencida + nota vigente sobre el mismo tema, `fresh_only` deja una sola, y `status` cuenta bien.

Bump del plugin a `0.3.0` (cambio de contrato de las tools). Commit.

---

### Task 11: Documentación, bench y release

**Files:** `README.md`, `README.es.md`, `CHANGELOG.md`, `bench/bench_search.py`, `skills/obsidian-rag/SKILL.md`

1. README (los dos idiomas): sección "Vigencia" con el ejemplo de frontmatter, la semántica de la fecha sin hora, qué hace el `×0.75`, `--fresh-only`, `obsn stale`, y una línea que diga que el nombre y la semántica son los de OKF v0.2 §5.5 (una nota compatible no necesita traducción).
2. `bench_search.py`: agregar dos casos que hoy no existen — la misma consulta contra una nota vencida y una vigente, esperando que gane la vigente; y una consulta que sólo existe en una nota vencida, esperando que **sí** aparezca (penalizada, no invisible).
3. CHANGELOG `[Unreleased]` → `### Added` con la feature y el cambio de esquema.
4. `scripts/release.sh 0.5.0`: cambio de esquema del índice ⇒ minor, según la política escrita.

---

## Riesgos y trampas conocidas

| Riesgo | Mitigación |
|---|---|
| `stale_after` mal escrito (fecha inválida) se ignora en silencio | contarlo en `status` como `sin_fecha` + una línea a stderr en `build` con el path, para que no se descubra seis meses después |
| Un sync (ovaultd, Seafile) reescribe la nota y le cambia el `mtime` | el ranking **no** usa `mtime`, sólo el campo declarado; `mtime` sigue siendo sólo señal de rebuild |
| El `×0.75` es un número elegido a dedo | el gate lo mide: si el top-5 del banco baja, se sube; el valor queda en el README |
| Confundir "vencida" con "no indexada" | son campos distintos: `vencida` viene de la nota, `sin_chunks` del índice; no se mezclan en `status` |
| Notas sin frontmatter (la mayoría) | quedan en `sin_fecha` y no se penalizan; la feature es opt-in por nota, nunca global |

## Fuera de alcance (YAGNI)

- TTL automático por carpeta o por defecto global: castiga notas que no eligieron participar.
- `verified`/trust tiers de OKF: son de la capa de autoría, no del retrieval.
- UI o vista en Obsidian: el campo se escribe a mano o con una plantilla.
- Fechas relativas tipo `stale_after: +6M`: la spec usa instantes absolutos y una fecha relativa se vuelve mentira al día siguiente de escribirla.

## Criterio de aceptación

1. `python -m unittest discover -s tests -t .` verde, con tests nuevos de parser, migración, scoring, `status`, `stale` y `meta`.
2. `python tests/e2e_plugin.py` verde con los chequeos de vigencia.
3. `bash bench/gate.sh` verde, con los dos casos nuevos del punto 11.
4. En un índice real: `obsn status` muestra `vencidas` y `sin_fecha`; `obsn stale` lista lo vencido; una búsqueda híbrida devuelve `vencida` por hit.
5. `obsn reindex` sobre un índice viejo migra y rellena sin intervención manual.
