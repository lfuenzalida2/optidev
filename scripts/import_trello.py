#!/usr/bin/env python3
"""Importa un tablero de Trello (export JSON) a optidev usando la API REST.

Uso:
    python3 scripts/import_trello.py trello.json --url https://optidev.example --token odv_...
    python3 scripts/import_trello.py trello.json --url ... --token ... --dry-run

El JSON se baja desde Trello: Menú del tablero → Imprimir, exportar y
compartir → Exportar como JSON.

Qué hace:
- Solo importa tarjetas abiertas de listas abiertas (las archivadas no).
- Listas: las mapea por nombre con LIST_MAP (o `--list-map "Trello=Optidev"`);
  las que no tienen equivalente se crean en optidev con el mismo nombre.
- Etiquetas: si el nombre coincide con la key de un repositorio de optidev,
  se asigna como repositorio; las demás se ignoran (optidev no tiene
  etiquetas). Por eso conviene cargar los repositorios antes de importar.
- Quita el prefijo "[area] " del título cuando "area" es un repositorio.
- Comentarios: se recrean a nombre del dueño del token, con autor y fecha
  originales en el texto (el export trae como mucho las 1000 acciones más
  recientes del tablero, así que comentarios muy viejos pueden faltar).
- Adjuntos que son links a PRs de GitHub van a `prUrls`.

No es idempotente: correrlo dos veces duplica las tarjetas.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

LIST_MAP = {
    "lista de tareas": "Pendiente",
    "en proceso": "En proceso",
    "pr": "PR",
    "testing required": "Testing",
    "hecho": "Hecho",
}

PR_URL = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/\d+")


class Api:
    def __init__(self, url: str, token: str, dry_run: bool):
        self.base = url.rstrip("/") + "/api"
        self.token = token
        self.dry_run = dry_run
        self._fake_id = 0

    def call(self, method: str, path: str, body: dict | None = None):
        if self.dry_run and method != "GET":
            self._fake_id -= 1
            return {"id": self._fake_id, **(body or {})}
        req = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.load(resp).get("data")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise SystemExit(f"{method} {path} → {exc.code}: {detail}") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", help="JSON exportado de Trello")
    parser.add_argument("--url", required=True, help="URL base de optidev (ej: http://localhost)")
    parser.add_argument("--token", required=True, help="Token personal odv_...")
    parser.add_argument("--list-map", action="append", default=[], help='"Lista Trello=Lista optidev" (repetible)')
    parser.add_argument("--skip-list", action="append", default=[], help="Lista de Trello a no importar (repetible)")
    parser.add_argument("--dry-run", action="store_true", help="Muestra qué haría sin escribir nada")
    args = parser.parse_args()

    with open(args.export, encoding="utf-8") as fh:
        trello = json.load(fh)

    list_map = dict(LIST_MAP)
    for item in args.list_map:
        src, _, dst = item.partition("=")
        list_map[src.strip().lower()] = dst.strip()
    skip = {s.strip().lower() for s in args.skip_list}

    api = Api(args.url, args.token, args.dry_run)
    board = api.call("GET", "/board")
    lists_by_name = {lst["name"].lower(): lst["id"] for lst in board["lists"]}
    repo_keys = {r["key"].lower(): r["key"] for r in board["repositories"]}

    # Listas
    trello_lists = {lst["id"]: lst for lst in trello.get("lists", []) if not lst.get("closed")}
    target_list: dict[str, int] = {}
    for tl in sorted(trello_lists.values(), key=lambda lst: lst.get("pos", 0)):
        if tl["name"].strip().lower() in skip:
            continue
        name = list_map.get(tl["name"].strip().lower(), tl["name"].strip())
        if name.lower() not in lists_by_name:
            print(f"+ lista {name}")
            lists_by_name[name.lower()] = api.call("POST", "/lists", {"name": name})["id"]
        target_list[tl["id"]] = lists_by_name[name.lower()]

    # Etiquetas de Trello cuyo nombre es la key de un repositorio
    repo_by_label = {
        tl["id"]: repo_keys[(tl.get("name") or "").strip().lower()]
        for tl in trello.get("labels", [])
        if (tl.get("name") or "").strip().lower() in repo_keys
    }

    # Comentarios agrupados por tarjeta (el export los trae como acciones).
    comments: dict[str, list[dict]] = {}
    for action in trello.get("actions", []):
        if action.get("type") == "commentCard":
            card_id = action.get("data", {}).get("card", {}).get("id")
            comments.setdefault(card_id, []).append(action)

    cards = [
        c for c in trello.get("cards", [])
        if not c.get("closed") and c.get("idList") in target_list
    ]
    cards.sort(key=lambda c: (c["idList"], c.get("pos", 0)))
    print(f"Importando {len(cards)} tarjetas{' (dry run)' if args.dry_run else ''}")

    for tc in cards:
        repos = [repo_by_label[i] for i in tc.get("idLabels", []) if i in repo_by_label]
        title = tc["name"].strip()
        prefix = re.match(r"^\[([^\]]+)\]\s*", title)
        if prefix and prefix.group(1).strip().lower() in {k.lower() for k in repos}:
            title = title[prefix.end():] or title
        description = (tc.get("desc") or "").strip()
        description = (description + "\n\n" if description else "") + f"_Importada de Trello: {tc.get('shortUrl', '')}_"

        card = api.call("POST", "/cards", {
            "title": title[:255],
            "listId": target_list[tc["idList"]],
            "description": description,
            "repositories": repos,
        })
        print(f"  #{card['id']} {title}  {repos}")

        pr_urls = [a["url"] for a in tc.get("attachments", []) if PR_URL.match(a.get("url") or "")]
        if pr_urls:
            api.call("PATCH", f"/cards/{card['id']}", {"prUrls": pr_urls})
        for action in sorted(comments.get(tc["id"], []), key=lambda a: a.get("date", "")):
            author = action.get("memberCreator", {}).get("fullName") or "?"
            date = (action.get("date") or "")[:16].replace("T", " ")
            text = action.get("data", {}).get("text", "")
            api.call("POST", f"/cards/{card['id']}/comments", {"body": f"**{author}** (Trello, {date} UTC):\n\n{text}"})


if __name__ == "__main__":
    sys.exit(main())
