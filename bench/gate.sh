#!/usr/bin/env bash
# Gate de publicación de obsidian-rag: tests + E2E del CLI + banco de búsqueda.
#
#   bash bench/gate.sh          # sólo verificar (sale 1 si algo falla)
#   bash bench/gate.sh --push   # verificar y, si pasa, commitear y pushear
#
# El intérprete se puede forzar con PY=... (por defecto, el del venv de Hermes si
# existe, si no `python3`).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PY="${PY:-}"
if [ -z "$PY" ]; then
  if [ -x "$HOME/.hermes/hermes-agent/venv/bin/python" ]; then
    PY="$HOME/.hermes/hermes-agent/venv/bin/python"
  else
    PY="python3"
  fi
fi

# fastembed escupe un warning de pooling en cada arranque: no es parte del resultado.
filtro() { grep -v "UserWarning" | grep -v "return TextEmbedding"; }

echo "=== 1) tests unitarios ==="
unit=$("$PY" -m unittest discover -s tests -t . 2>&1 | filtro | grep -E "^(Ran|OK|FAILED|ERROR)")
echo "$unit"

echo
echo "=== 2) E2E del CLI ==="
e2e=$("$PY" tests/e2e_cli.py 2>&1 | filtro | grep -E "^(  FALLA|[0-9]+/[0-9]+ checks|E2E OK)")
echo "$e2e"

echo
echo "=== 3) banco de búsqueda ==="
bench=$("$PY" bench/bench_search.py 2>&1 | filtro | grep -E "^(POS|NEG|EXACT|falsos|acierto)")
echo "$bench"

echo
echo "=== veredicto ==="
falla=0
echo "$unit" | grep -q "^OK"                                     || { echo "FALLA: tests"; falla=1; }
echo "$e2e"  | grep -q "E2E OK"                                  || { echo "FALLA: e2e"; falla=1; }
echo "$bench" | grep -q "falsos positivos en NEG: 0/8"           || { echo "FALLA: falsos positivos en NEG"; falla=1; }
echo "$bench" | grep -qE "^EXACT .* +5 +4 +5 "                   || { echo "FALLA: EXACT top-5 != 5"; falla=1; }
echo "$bench" | grep -qE "^POS .* +10 +2 +2 "                    || { echo "FALLA: POS top-1 < 2 (regresión)"; falla=1; }

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
