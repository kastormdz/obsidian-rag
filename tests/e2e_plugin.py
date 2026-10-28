"""E2E del plugin obsidian-rag contra un vault temporal (nunca el real).

Carga el paquete como lo hace Hermes, registra con un ctx falso y llama a cada handler.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "obsidian-rag"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


class Ctx:
    def __init__(self):
        self.tools, self.skills, self.cfg = {}, [], {}

    def get_config(self, key, default=None):
        return self.cfg.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_skill(self, name, path, description="", **kw):
        self.skills.append((name, Path(path)))


def make_vault() -> Path:
    root = Path(tempfile.mkdtemp(prefix="obsn-plugin-", dir=os.environ.get("TMPDIR") or None))
    (root / ".obsidian").mkdir()
    (root / "01-Infra").mkdir()
    (root / "02-Personal").mkdir()
    (root / "01-Infra" / "keycloak.md").write_text(
        "# Keycloak\n\nFederación con OpenLDAP para 1.200 usuarios.\nSSO del corporativo.\n",
        encoding="utf-8")
    (root / "01-Infra" / "docker.md").write_text(
        "# Docker\n\nContenedores en Alpha, en el disco de datos.\n", encoding="utf-8")
    (root / "02-Personal" / "viaje.md").write_text(
        "# Viaje\n\nPasajes a la costa en enero.\n", encoding="utf-8")
    return root


def main():
    root = make_vault()
    idx = root / "index.db"
    env = dict(os.environ, OBS_RAG_VAULT=str(root), OBS_RAG_INDEX=str(idx))
    os.environ.update(OBS_RAG_VAULT=str(root), OBS_RAG_INDEX=str(idx))

    r = subprocess.run(["obsn", "reindex", "--force"], capture_output=True, text=True, env=env)
    check("reindex del vault temporal", r.returncode == 0, (r.stdout or r.stderr).strip()[-90:])

    spec = importlib.util.spec_from_file_location(
        "obsplugin", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["obsplugin"] = mod
    spec.loader.exec_module(mod)

    ctx = Ctx()
    mod.register(ctx)
    check("registra 7 tools", len(ctx.tools) == 7, sorted(ctx.tools))
    check("registra la skill", len(ctx.skills) == 1 and ctx.skills[0][1].exists(), ctx.skills)
    check("toolset obsidian_vault", {t["toolset"] for t in ctx.tools.values()} == {"obsidian_vault"})

    call = lambda name, args: json.loads(ctx.tools[name]["handler"](args))

    # --- status
    s = call("vault_status", {})
    check("status: ok y 3 notas", s.get("ok") and s.get("notas") == 3, f"notas={s.get('notas')} obsn={s.get('obsn_version')}")

    # --- search híbrida (con un término que existe: en un vault de 3 notas una paráfrasis
    # sin ninguna palabra en común queda debajo del piso semántico, y eso es del motor, no
    # del plugin; ver la nota de calibración en README)
    h = call("vault_search", {"query": "federación con openldap", "limit": 5})
    paths = [x["path"] for x in h.get("hits", [])]
    check("search hybrid encuentra keycloak", h.get("ok") and "01-Infra/keycloak.md" in paths[:2], paths)
    check("search hybrid deduplica por nota", len(paths) == len(set(paths)), f"{len(paths)} hits")
    check("search hybrid devuelve snippet y score",
          bool(h["hits"]) and "score" in h["hits"][0] and h["hits"][0]["snippet"], h["hits"][0].get("snippet", "")[:50] if h.get("hits") else "")

    # --- search exacta con filtro de carpeta
    e = call("vault_search", {"query": "la costa", "mode": "exact"})
    check("search exact encuentra la nota personal",
          e.get("ok") and [x["path"] for x in e["hits"]] == ["02-Personal/viaje.md"], e.get("hits"))

    # --- read
    rd = call("vault_read", {"path": "01-Infra/keycloak.md"})
    check("read devuelve el contenido", rd.get("ok") and "OpenLDAP" in rd.get("content", ""), f"{rd.get('chars')} chars")

    # --- write create (y que el path sin .md funcione)
    w = call("vault_write", {"path": "01-Infra/ldap", "content": "# LDAP\n\nDirectorio corporativo.\n"})
    check("write crea con path sin extension", w.get("ok") and (root / "01-Infra" / "ldap.md").exists(), w.get("output"))

    # --- append
    a = call("vault_write", {"path": "01-Infra/ldap.md", "content": "\n1.200 usuarios.\n", "mode": "append"})
    body = (root / "01-Infra" / "ldap.md").read_text(encoding="utf-8")
    check("write append agrega sin pisar", a.get("ok") and "Directorio" in body and "1.200" in body, body.replace("\n", " ")[:60])

    # --- replace
    rp = call("vault_write", {"path": "01-Infra/ldap.md", "content": "1.400 usuarios.", "mode": "replace", "old": "1.200 usuarios."})
    body = (root / "01-Infra" / "ldap.md").read_text(encoding="utf-8")
    check("write replace sustituye", rp.get("ok") and "1.400" in body and "1.200" not in body, body.replace("\n", " ")[:60])

    # --- replace sin old: error claro, no excepcion
    bad = call("vault_write", {"path": "01-Infra/ldap.md", "content": "x", "mode": "replace"})
    check("replace sin old falla limpio", bad.get("ok") is False and "old" in bad.get("error", ""), bad.get("error"))

    # --- list
    ln = call("vault_list", {"mode": "notes", "folder": "01-Infra"})
    check("list notes filtra por carpeta", ln.get("ok") and all(p.startswith("01-Infra/") for p in ln["notes"]), ln.get("notes"))
    lf = call("vault_list", {"mode": "folders"})
    check("list folders trae conteos", lf.get("ok") and any(f["folder"] == "01-Infra" for f in lf["folders"]), lf.get("folders"))
    lt = call("vault_list", {"mode": "tags"})
    check("list tags responde", lt.get("ok") and isinstance(lt.get("tags"), list), f"{len(lt.get('tags') or [])} tags")

    # --- move
    m = call("vault_move", {"from": "01-Infra/docker.md", "to": "01-Infra/containers.md"})
    check("move renombra", m.get("ok") and (root / "01-Infra" / "containers.md").exists()
          and not (root / "01-Infra" / "docker.md").exists(), m.get("output"))

    # --- move a destino existente: falla, no pisa
    m2 = call("vault_move", {"from": "01-Infra/containers.md", "to": "01-Infra/keycloak.md"})
    keep = (root / "01-Infra" / "keycloak.md").read_text(encoding="utf-8")
    check("move no pisa el destino", m2.get("ok") is False and "Keycloak" in keep, m2.get("error", "")[:60])

    # --- delete a papelera
    d = call("vault_delete", {"path": "02-Personal/viaje.md"})
    trash = list((root / ".trash").glob("*")) if (root / ".trash").exists() else []
    check("delete manda a .trash", d.get("ok") and d.get("recoverable") and trash,
          [p.name for p in trash])

    # --- path vacio / inexistente: error JSON, nunca excepcion
    check("path vacio falla limpio", call("vault_read", {"path": "  "}).get("ok") is False)
    nf = call("vault_read", {"path": "no/existe.md"})
    check("nota inexistente falla limpio", nf.get("ok") is False and bool(nf.get("error")), nf.get("error", "")[:60])

    # --- nada escribio fuera del vault
    outside = list(Path(tempfile.gettempdir()).glob("obsn-plugin-*.md"))
    check("no escribio fuera del vault", not outside, outside)

    # --- status después del borrado: 3 originales + ldap.md - viaje.md = 3
    import time
    time.sleep(4)
    s2 = call("vault_status", {})
    check("status refleja escritura y borrado", s2.get("notas") == 3, f"notas={s2.get('notas')} (esperado 3)")

    shutil.rmtree(root, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
