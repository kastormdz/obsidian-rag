#!/usr/bin/env bash
# release.sh — corta una versión de obsidian-rag: bump, changelog, tag, push y release.
#
#   scripts/release.sh --check        # valida precondiciones y muestra qué se publicaría
#   scripts/release.sh --how          # explica el procedimiento
#   scripts/release.sh 0.2.0          # corta la 0.2.0
#
# Sale con 0 si publicó (o si --check pasó) y con 1 si falta algo. No inventa merges y no
# publica nada si el gate no está verde.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
CHANGELOG="$REPO/CHANGELOG.md"
PYPROJECT="$REPO/pyproject.toml"

# Sin agente SSH: el repo usa llave propia y esto también puede correr sin terminal.
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o IdentityAgent=none -o BatchMode=yes}"

rojo() { printf '\033[31m%s\033[0m\n' "$*" >&2; }
verde() { printf '\033[32m%s\033[0m\n' "$*"; }
die() { rojo "release: $*"; exit 1; }

how() {
    cat <<'TXT'
release.sh corta una versión y la publica.

1. Verifica que estés en main, con el árbol limpio y sin commits sin pushear.
2. Corre el gate completo (tests, E2E y banco). Si falla, no toca nada.
3. Sube `version` en pyproject.toml.
4. Abre la sección de la versión en CHANGELOG.md con la fecha de hoy, debajo de
   [Unreleased]. Si [Unreleased] está vacío, se niega: no hay nada que publicar.
5. Commit `chore(release): vX.Y.Z`, tag anotado `vX.Y.Z` y push de main más el tag.
6. Crea el release en GitHub con las notas sacadas del changelog.
7. Reinstala el paquete editable en el mismo intérprete que corre obsn y verifica
   que `obsn --version` diga la versión nueva.

SemVer mientras estemos en 0.x: un cambio de esquema del índice o del ranking sube la
minor (obliga a reconstruir), un fix sube la patch.

El changelog se escribe a mano en [Unreleased] con las categorías de Keep a Changelog
(Added, Changed, Fixed, Security). El script sólo mueve esa sección a la versión nueva.
TXT
}

version_actual() {
    sed -nE 's/^version *= *"([^"]+)".*/\1/p' "$PYPROJECT" | head -1
}

seccion() {  # $1 = etiqueta sin corchetes, ej: Unreleased
    awk -v v="$1" '
        $0 ~ "^## \\[" v "\\]" { dentro=1; next }
        dentro && /^## \[/ { exit }
        dentro { print }
    ' "$CHANGELOG"
}

precondiciones() {
    command -v gh >/dev/null || die "falta gh (GitHub CLI)"
    gh auth status >/dev/null 2>&1 || die "gh no está autenticado"
    [ -f "$CHANGELOG" ] || die "no existe CHANGELOG.md"
    [ -f "$PYPROJECT" ] || die "no existe pyproject.toml"
    if [ -n "$(git status --porcelain)" ]; then
        rojo "hay cambios sin commitear:"
        git status --short | sed 's/^/  /' >&2
        exit 1
    fi
    [ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || die "no estás en main"
    git fetch --quiet --tags origin || die "no pude hacer git fetch"
    [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
        || die "main local y origin/main no coinciden (¿commits sin pushear?)"

    OBSN="$(command -v obsn || true)"
    [ -n "$OBSN" ] || die "obsn no está en PATH"
    PYBIN="$(resolver_python)"
    "$PYBIN" -c "import rag" 2>/dev/null \
        || die "$PYBIN no puede importar rag (¿la instalación es editable?)"
}

resolver_python() {
    local candidato
    # Puede ser un console-script de pip (`#!<venv>/bin/python`) o un launcher de bash que
    # hace `exec "$HOME/.../python" -m ...`. En los dos casos la ruta está en el archivo,
    # pero en el launcher viene con `$HOME` sin expandir: hay que expandirla antes de
    # probarla, o `-x` falla y termina usando el python3 del PATH (otro venv).
    while IFS= read -r candidato; do
        candidato="$(printf '%s' "$candidato" \
            | sed "s|\${HOME}|$HOME|g; s|\$HOME|$HOME|g")"
        case "$candidato" in
            "~/"*) candidato="$HOME/${candidato#\~/}" ;;
        esac
        if [ -x "$candidato" ]; then
            printf '%s' "$candidato"
            return 0
        fi
    done < <(grep -hoE '[^ "'"'"']*/python[0-9.]*' "$OBSN" 2>/dev/null)
    command -v python3
}

main() {
    local version="" check=0
    case "${1:-}" in
        --how) how; exit 0 ;;
        --check) check=1; version="${2:-}" ;;
        "") die "falta la versión. Uso: scripts/release.sh 0.2.0  (o --check / --how)" ;;
        *) version="$1" ;;
    esac

    precondiciones
    local antes
    antes="$(version_actual)"
    [ -n "$antes" ] || die "no pude leer la versión de pyproject.toml"

    echo "instalada: $antes"
    echo "intérprete: $PYBIN"
    echo "tags: $(git tag -l 'v*' | sort -V | tr '\n' ' ' | sed 's/ $//' | grep . || echo '(ninguno)')"

    if [ "$check" = 1 ]; then
        echo
        echo "== pendiente en [Unreleased] =="
        seccion Unreleased | sed '/^$/d' | sed 's/^/  /' | head -40
        if [ -n "$version" ]; then
            [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "'$version' no es SemVer (X.Y.Z)"
            git rev-parse -q --verify "refs/tags/v$version" >/dev/null \
                && die "el tag v$version ya existe"
            echo
            echo "publicaría v$version (hoy es $(date +%F))"
        fi
        verde "check OK: precondiciones en orden"
        exit 0
    fi

    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "'$version' no es SemVer (X.Y.Z)"
    [ "$version" != "$antes" ] || die "pyproject ya dice $version"
    git rev-parse -q --verify "refs/tags/v$version" >/dev/null && die "el tag v$version ya existe"

    if ! "$PYBIN" - "$antes" "$version" <<'PY'
import sys
a = tuple(int(x) for x in sys.argv[1].split("."))
b = tuple(int(x) for x in sys.argv[2].split("."))
raise SystemExit(0 if b > a else 1)
PY
    then
        die "la versión nueva tiene que ser mayor que $antes"
    fi

    if [ -z "$(seccion Unreleased | tr -d '[:space:]')" ]; then
        die "[Unreleased] está vacío: escribí el changelog antes de cortar la versión"
    fi

    echo
    echo "== gate =="
    bash bench/gate.sh || die "el gate no pasó; no se toca nada"

    echo
    echo "== changelog y versión =="
    "$PYBIN" - "$PYPROJECT" "$version" <<'PY'
import pathlib
import re
import sys

ruta, version = pathlib.Path(sys.argv[1]), sys.argv[2]
texto = ruta.read_text(encoding="utf-8")
nuevo, cambios = re.subn(r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"', texto, count=1)
if cambios != 1:
    raise SystemExit('no encontré la línea version = "..." en pyproject.toml')
ruta.write_text(nuevo, encoding="utf-8")
PY
    local fecha
    fecha="$(date +%F)"
    awk -v nueva="## [$version] - $fecha" '
        /^## \[Unreleased\]/ && !hecho { print; print ""; print nueva; hecho=1; next }
        { print }
    ' "$CHANGELOG" > "$CHANGELOG.nuevo"
    mv "$CHANGELOG.nuevo" "$CHANGELOG"

    git add "$PYPROJECT" "$CHANGELOG"
    git commit -q -m "chore(release): v$version"
    git tag -a "v$version" -m "v$version"
    echo "commit y tag listos"

    git push --quiet origin main --follow-tags || die "el push falló (¿tag ya publicado?)"

    local notas
    notas="$(mktemp)"
    seccion "$version" > "$notas"
    gh release create "v$version" --title "v$version" --notes-file "$notas" --verify-tag \
        || { rm -f "$notas"; die "no pude crear el release en GitHub"; }
    rm -f "$notas"

    echo
    echo "== reinstalar para que obsn --version lo refleje =="
    "$PYBIN" -m pip install -e "$REPO" --quiet || die "pip install -e falló"
    local instalada
    instalada="$("$OBSN" --version 2>/dev/null | awk '{print $2}')"
    [ "$instalada" = "$version" ] || rojo "ojo: obsn --version dice '$instalada'"

    local url
    url="$(gh release view "v$version" --json url -q .url 2>/dev/null || true)"
    verde "release v$version publicada${url:+: $url}"
}

main "$@"
