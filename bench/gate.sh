#!/usr/bin/env bash
# Gate de publicación de obsidian-rag: tests + E2E del CLI + E2E del plugin + banco de
# búsqueda + banco de vigencia.
#
#   bash bench/gate.sh          # sólo verificar (sale 1 si algo falla)
#   bash bench/gate.sh --push   # verificar y, si pasa, commitear y pushear
#   BENCH_REAL=1 bash bench/gate.sh   # medir la búsqueda contra TU vault/índice real
#
# El banco de búsqueda usa `--fixture` por defecto: construye un vault temporal desde
# `bench/fixture/` y mide contra él. Así el gate corre en una máquina limpia o en CI, sin
# depender de las notas ni del índice de quien lo escribe. `BENCH_REAL=1` vuelve al modo
# histórico (índice real), útil para comparar contra tu corpus.
#
# El intérprete se puede forzar con PY=... (por defecto, el del venv de Hermes si
# existe, si no `python3`).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PY="${PY:-}"
# Un OBS_RAG_VAULT / OBS_RAG_INDEX exportado por una prueba anterior haría que el modo
# real mida un índice temporal (o vacío) sin explicar por qué. El modo fixture no los usa.
unset OBS_RAG_VAULT OBS_RAG_INDEX OBS_RAG_DEBOUNCE OBS_RAG_NO_ASYNC
if [ -z "$PY" ]; then
  if [ -x "$HOME/.hermes/hermes-agent/venv/bin/python" ]; then
    PY="$HOME/.hermes/hermes-agent/venv/bin/python"
  else
    PY="python3"
  fi
fi

bench_flag="--fixture"
if [ "${BENCH_REAL:-0}" = "1" ]; then
  bench_flag=""
fi

# fastembed escupe un warning de pooling en cada arranque: no es parte del resultado.
filtro() { grep -v "UserWarning" | grep -v "return TextEmbedding"; }

echo "=== 1) tests unitarios ==="
unit=$("$PY" -W error::ResourceWarning -m unittest discover -s tests -t . 2>&1 \
  | filtro | grep -E "^(Ran|OK|FAILED|ERROR)")
echo "$unit"

echo
echo "=== 2) E2E del CLI ==="
e2e=$("$PY" tests/e2e_cli.py 2>&1 | filtro | grep -E "^(  FALLA|[0-9]+/[0-9]+ checks|E2E OK)")
echo "$e2e"

echo
echo "=== 2b) E2E del plugin ==="
plugin=$("$PY" tests/e2e_plugin.py 2>&1 | filtro | grep -E "^(FAIL|[0-9]+/[0-9]+ OK)")
echo "$plugin"

echo
echo "=== 3) banco de búsqueda (${bench_flag:-índice real}) ==="
bench=$("$PY" bench/bench_search.py $bench_flag 2>&1 | filtro \
  | grep -E "^(POS|NEG|EXACT|falsos|acierto)")
echo "$bench"
# Valores para el veredicto: del JSON, no de la tabla (los nombres de grupo tienen
# espacios y las columnas se corren: parsear prosa es cómo se rompe un gate).
bench_json=$("$PY" bench/bench_search.py $bench_flag --json 2>&1 | filtro | tail -1)

echo
echo "=== 4) banco de vigencia ==="
vig=$("$PY" bench/bench_vigencia.py 2>&1 | filtro | grep -E "^(pares|build)")
echo "$vig"
vig_json=$("$PY" bench/bench_vigencia.py --json 2>&1 | filtro | tail -1)

echo
echo "=== veredicto ==="
falla=0
valor() { echo "$bench_json" | "$PY" -c "import json,sys;print(json.load(sys.stdin)$1)" 2>/dev/null; }
vigvalor() { echo "$vig_json" | "$PY" -c "import json,sys;print(json.load(sys.stdin)$1)" 2>/dev/null; }
pos1=$(valor "['pos']['top1']")
pos5=$(valor "['pos']['top5']")
exact5=$(valor "['exact']['top5']")
negn=$(valor "['neg']['n']")
negv=$(valor "['neg']['vacios']")
vpares=$(vigvalor "['pares']")
vtop1=$(vigvalor "['top1_vigente']")
vvis=$(vigvalor "['vencida_visible']")
vfresh=$(vigvalor "['fresh_only_limpio']")
echo "$unit"   | grep -q "^OK"                           || { echo "FALLA: tests"; falla=1; }
echo "$e2e"    | grep -q "E2E OK"                        || { echo "FALLA: e2e CLI"; falla=1; }
# `38/38 OK` y no `31/38 OK`: el backreference exige que los dos números coincidan.
echo "$plugin" | grep -qE "^([0-9]+)/\1 OK"              || { echo "FALLA: e2e plugin"; falla=1; }
[ "${negn:-0}" -gt 0 ]                                  || { echo "FALLA: banco de búsqueda no corrió"; falla=1; }
[ "${negv:-0}" -eq "${negn:-1}" ]                       || { echo "FALLA: NEG con falsos positivos ($negv/$negn vacíos)"; falla=1; }
[ "${exact5:-0}" -ge 5 ]  || { echo "FALLA: EXACT top-5 < 5"; falla=1; }
[ "${pos1:-0}"  -ge 2 ]   || { echo "FALLA: POS top-1 < 2 (regresión)"; falla=1; }
[ "${pos5:-0}"  -ge 4 ]   || { echo "FALLA: POS top-5 < 4 (regresión)"; falla=1; }
[ "${vpares:-0}" -gt 0 ]  || { echo "FALLA: banco de vigencia no corrió"; falla=1; }
[ "${vtop1:-0}"  -eq "${vpares:-1}" ]  || { echo "FALLA: la vencida gana el top-1 en algún par"; falla=1; }
[ "${vvis:-0}"   -eq "${vpares:-1}" ]  || { echo "FALLA: la vencida desapareció (tiene que quedar)"; falla=1; }
[ "${vfresh:-0}" -eq "${vpares:-1}" ]  || { echo "FALLA: fresh_only no filtró en algún par"; falla=1; }

if [ "$falla" -ne 0 ]; then
  echo "GATE NO PASA."
  exit 1
fi
echo "GATE OK"

if [ "${1:-}" != "--push" ]; then
  exit 0
fi
git add -A || exit 1
if git diff --cached --quiet; then
  echo "(sin cambios para commitear)"
else
  git -c core.hooksPath=.git/hooks commit -q -m "${MSG:-chore: actualización}" || exit 1
fi
GIT_SSH_COMMAND="ssh -o IdentityAgent=none" git push origin main
echo "PUSHEADO: $(git log --oneline -1)"
