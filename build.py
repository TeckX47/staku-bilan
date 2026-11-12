#!/usr/bin/env python3
"""Génère la page de graphiques du suivi STAKU à partir des bases Notion.

Usage :
  NOTION_TOKEN=... python3 build.py --out site          lit Notion, écrit site/index.html
  python3 build.py --sample sample.json --out site      rendu de test, sans Notion

Aucune dépendance : bibliothèque standard de Python uniquement.
"""
import argparse
import datetime
import html
import json
import os
import sys
import urllib.error
import urllib.request

DATABASES = {
    "taches": "484de2aeeb6d4d61aa86e90aecbb0773",
    "jalons": "8ab027949f34498f8db0cfb80a3996cc",
    "catalogue": "bf17f7fd18da468cb5f97e4e1c2d3af5",
    "registre": "0af51c1c87fd4e79829abee7e4ef62a1",
}
NOTION_URL = "https://app.notion.com/p/415240ba180481ffaedfda60758dbb39"
STATUTS = ["Backlog", "À faire", "En cours", "En test", "Terminé"]
ETATS = ["À concevoir", "Dans le code", "Testé"]
PROBAS = ["3 · Forte", "2 · Moyenne", "1 · Faible"]
IMPACTS = ["1 · Faible", "2 · Moyen", "3 · Fort"]
RISQUE_CLOS = ("Risque maîtrisé", "Tranché")


# ---------------------------------------------------------------- Notion

def notion_query(database_id, token):
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:300]
            sys.exit(
                f"Notion a refusé la lecture de la base {database_id} ({err.code}). "
                f"Vérifiez que la page du dossier est partagée avec l'intégration. {detail}"
            )
        rows.extend(data["results"])
        if not data.get("has_more"):
            return rows
        cursor = data["next_cursor"]


def value(prop):
    """Valeur simple d'une propriété Notion, quel que soit son type."""
    if prop is None:
        return None
    kind = prop["type"]
    val = prop[kind]
    if kind in ("select", "status"):
        return val["name"] if val else None
    if kind in ("title", "rich_text"):
        return "".join(part["plain_text"] for part in val).strip()
    if kind == "relation":
        return [item["id"] for item in val]
    if kind == "multi_select":
        return [item["name"] for item in val]
    if kind == "formula":
        return val.get(val["type"])
    return val


def fetch(token):
    raw = {name: notion_query(db, token) for name, db in DATABASES.items()}

    def rows(name, fields):
        out = []
        for page in raw[name]:
            props = page["properties"]
            row = {field: value(props.get(field)) for field in fields}
            row["id"] = page["id"]
            out.append(row)
        return out

    return {
        "taches": rows("taches", ["Statut", "Catégorie", "Jalon", "En attente de"]),
        "jalons": rows("jalons", ["Jalon", "Ordre", "État"]),
        "catalogue": rows("catalogue", ["Type", "État"]),
        "registre": rows("registre", ["Type", "Statut", "Section du GDD", "Probabilité", "Impact", "Code"]),
    }


# ---------------------------------------------------------------- calculs

def count_by(rows, key, order):
    out = {name: 0 for name in order}
    for row in rows:
        if row.get(key) in out:
            out[row[key]] += 1
    return out


def aggregate(data):
    taches = data["taches"]
    jalons = sorted(
        (j for j in data["jalons"] if j.get("Jalon")),
        key=lambda j: (j.get("Ordre") is None, j.get("Ordre") or 0),
    )
    par_jalon = []
    for jalon in jalons:
        liees = [t for t in taches if jalon["id"] in (t.get("Jalon") or [])]
        par_jalon.append({"label": jalon["Jalon"], "counts": count_by(liees, "Statut", STATUTS)})
    sans_jalon = [t for t in taches if not t.get("Jalon")]
    if sans_jalon:
        par_jalon.append({"label": "Sans jalon", "counts": count_by(sans_jalon, "Statut", STATUTS)})

    categories = sorted({t.get("Catégorie") or "Sans catégorie" for t in taches})
    par_categorie = []
    for cat in categories:
        liees = [t for t in taches if (t.get("Catégorie") or "Sans catégorie") == cat]
        par_categorie.append({"label": cat, "counts": count_by(liees, "Statut", STATUTS)})
    par_categorie.sort(key=lambda r: -sum(r["counts"].values()))

    registre = data["registre"]
    questions = [r for r in registre if r.get("Type") == "Question"]
    decisions = [r for r in registre if r.get("Type") == "Décision"]
    risques = [r for r in registre if r.get("Type") == "Risque"]
    ouvertes = [q for q in questions if q.get("Statut") != "Tranché"]
    sections = {}
    for q in ouvertes:
        sec = q.get("Section du GDD") or "Sans section"
        sections[sec] = sections.get(sec, 0) + 1
    par_section = sorted(sections.items(), key=lambda kv: -kv[1])

    matrice = {p: {i: [] for i in IMPACTS} for p in PROBAS}
    for r in risques:
        if r.get("Statut") in RISQUE_CLOS:
            continue
        if r.get("Probabilité") in matrice and r.get("Impact") in IMPACTS:
            matrice[r["Probabilité"]][r["Impact"]].append(r.get("Code") or "?")

    types = {}
    for item in data["catalogue"]:
        if not item.get("État") or item.get("État") not in ETATS:
            continue
        typ = item.get("Type") or "Autre"
        types.setdefault(typ, {e: 0 for e in ETATS})[item["État"]] += 1
    par_type = sorted(
        ({"label": k, "counts": v} for k, v in types.items()),
        key=lambda r: -sum(r["counts"].values()),
    )

    statuts = count_by(taches, "Statut", STATUTS)
    return {
        "taches": {
            "total": len(taches),
            "statuts": statuts,
            "en_attente": sum(1 for t in taches if (t.get("En attente de") or "").strip()),
        },
        "par_jalon": par_jalon,
        "par_categorie": par_categorie,
        "questions": {"total": len(questions), "tranchees": len(questions) - len(ouvertes)},
        "decisions": {
            "total": len(decisions),
            "actees": sum(1 for d in decisions if d.get("Statut") == "Tranché"),
        },
        "risques": {
            "total": len(risques),
            "maitrises": sum(1 for r in risques if r.get("Statut") in RISQUE_CLOS),
        },
        "par_section": par_section,
        "matrice": matrice,
        "par_type": par_type,
    }


def update_history(history, agg, today):
    point = {
        "date": today,
        "taches_terminees": agg["taches"]["statuts"]["Terminé"],
        "taches_total": agg["taches"]["total"],
        "questions_tranchees": agg["questions"]["tranchees"],
        "questions_total": agg["questions"]["total"],
    }
    history = [h for h in history if h.get("date") != today] + [point]
    return sorted(history, key=lambda h: h["date"])


def load_history(site_url):
    if not site_url:
        return []
    try:
        with urllib.request.urlopen(site_url.rstrip("/") + "/history.json", timeout=30) as resp:
            data = json.load(resp)
            return data if isinstance(data, list) else []
    except Exception:
        return []


# ---------------------------------------------------------------- rendu

def esc(text):
    return html.escape(str(text), quote=True)


def pct(part, total):
    return round(100 * part / total) if total else 0


def tile(label, part, total, detail):
    p = pct(part, total)
    return (
        f'<div class="tile"><div class="tile-label">{esc(label)}</div>'
        f'<div class="tile-value">{p} %</div>'
        f'<div class="meter" role="img" aria-label="{p} %"><span style="width:{p}%"></span></div>'
        f'<div class="tile-detail">{esc(detail)}</div></div>'
    )


def legend(names, classes):
    items = "".join(
        f'<span class="key"><i class="{cls}"></i>{esc(name)}</span>' for name, cls in zip(names, classes)
    )
    return f'<div class="legend">{items}</div>'


def stacked_bars(rows, order, classes, label_w=130, width=500, row_h=28):
    """Barres horizontales empilées. rows = [{label, counts{nom: n}}]."""
    bar_h, right = row_h - 12 if row_h >= 28 else row_h - 7, 40 if row_h >= 28 else 30
    plot = width - label_w - right
    biggest = max((sum(r["counts"].values()) for r in rows), default=0) or 1
    height = row_h * len(rows) + 6
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    for index, row in enumerate(rows):
        y = index * row_h + 7
        total = sum(row["counts"].values())
        parts.append(
            f'<text class="lab" x="{label_w - 10}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="end">{esc(row["label"])}</text>'
        )
        x = float(label_w)
        for name, cls in zip(order, classes):
            n = row["counts"].get(name, 0)
            if not n:
                continue
            w = plot * n / biggest
            tip = f"{row['label']} · {name} : {n} sur {total}"
            parts.append(
                f'<rect class="{cls} mark" x="{x:.1f}" y="{y}" width="{max(w - 2, 1):.1f}" height="{bar_h}" '
                f'rx="3" data-tip="{esc(tip)}"><title>{esc(tip)}</title></rect>'
            )
            x += w
        parts.append(f'<text class="val" x="{x + 6:.1f}" y="{y + bar_h / 2 + 4:.1f}">{total}</text>')
    parts.append("</svg>")
    return "".join(parts)


FIT_W = 384  # largeur utile d'une carte dans la vue « tout sur un écran »


def fit_bars(rows, order, classes, label_w=162):
    """Version compacte, à l'échelle 1, pour la vue « tout sur un écran »."""
    return f'<div class="only-fit">{stacked_bars(rows, order, classes, label_w, FIT_W, 20)}</div>'


def responsive_bars(rows, order, classes, wide=True):
    """Version large pour l'écran, étroite pour le téléphone, compacte pour la vue sans défilement."""
    if not wide:
        return f'<div class="not-fit">{stacked_bars(rows, order, classes)}</div>' + fit_bars(rows, order, classes, 96)
    return (
        f'<div class="only-wide">{stacked_bars(rows, order, classes, 210, 1060)}</div>'
        f'<div class="only-narrow">{stacked_bars(rows, order, classes, 150, 500)}</div>'
        + fit_bars(rows, order, classes)
    )


def simple_bars(pairs, label_w=130):
    rows = [{"label": label, "counts": {"n": n}} for label, n in pairs]
    return (
        f'<div class="not-fit">{stacked_bars(rows, ["n"], ["s-solo"], label_w)}</div>'
        + fit_bars(rows, ["n"], ["s-solo"], 110)
    )


def table(headers, rows):
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows)
    return (
        "<details><summary>Voir les chiffres</summary>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></details>"
    )


def stacked_table(rows, order):
    return table(
        [""] + order + ["Total"],
        [[r["label"]] + [r["counts"].get(o, 0) for o in order] + [sum(r["counts"].values())] for r in rows],
    )


def risk_matrix(matrice):
    biggest = max((len(codes) for row in matrice.values() for codes in row.values()), default=0) or 1
    cells = ['<div class="matrix"><div class="m-corner"></div>']
    cells += [f'<div class="m-head">Impact {esc(i.split(" · ")[1].lower())}</div>' for i in IMPACTS]
    for proba in PROBAS:
        cells.append(f'<div class="m-side">Probabilité {esc(proba.split(" · ")[1].lower())}</div>')
        for impact in IMPACTS:
            codes = sorted(matrice[proba][impact])
            level = 0 if not codes else 1 + round(3 * len(codes) / biggest)
            tip = f"{len(codes)} risque(s) : {', '.join(codes)}" if codes else "Aucun risque"
            cells.append(
                f'<div class="m-cell lvl{level} mark" data-tip="{esc(tip)}" title="{esc(tip)}">'
                f'<b>{len(codes) or ""}</b><span>{esc(" ".join(codes))}</span></div>'
            )
    cells.append("</div>")
    return "".join(cells)


def history_chart(history):
    if len(history) < 2:
        return '<p class="note">La courbe apparaîtra dès le deuxième jour de relevé.</p>'
    return (
        f'<div class="not-fit">{history_svg(history, 500, 220)}</div>'
        f'<div class="only-fit">{history_svg(history, FIT_W, 190)}</div>'
    )


def history_svg(history, width, height):
    left, right, top, bottom = 34, 96, 14, 26
    top_value = max(h["taches_total"] for h in history) or 1
    n = len(history)

    def px(i):
        return left + (width - left - right) * i / (n - 1)

    def py(v):
        return top + (height - top - bottom) * (1 - v / top_value)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    for frac in (0, 0.5, 1):
        v = round(top_value * frac)
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{py(v):.1f}" y2="{py(v):.1f}"/>')
        parts.append(f'<text class="axis" x="{left - 6}" y="{py(v) + 4:.1f}" text-anchor="end">{v}</text>')
    for key, cls, name in (("taches_total", "l-total", "Total"), ("taches_terminees", "l-done", "Terminées")):
        pts = " ".join(f"{px(i):.1f},{py(h[key]):.1f}" for i, h in enumerate(history))
        parts.append(f'<polyline class="line {cls}" points="{pts}"/>')
        last = history[-1][key]
        parts.append(f'<text class="lab" x="{px(n - 1) + 6:.1f}" y="{py(last) + 4:.1f}">{esc(name)} {last}</text>')
    for i, h in enumerate(history):
        tip = f"{h['date']} · {h['taches_terminees']} terminées sur {h['taches_total']}"
        parts.append(
            f'<circle class="dot mark" cx="{px(i):.1f}" cy="{py(h["taches_terminees"]):.1f}" r="4" '
            f'data-tip="{esc(tip)}"><title>{esc(tip)}</title></circle>'
        )
    parts.append(f'<text class="axis" x="{left}" y="{height - 6}">{esc(history[0]["date"])}</text>')
    parts.append(
        f'<text class="axis" x="{width - right}" y="{height - 6}" text-anchor="end">{esc(history[-1]["date"])}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def card(title, subtitle, body, wide=False):
    cls = "card wide" if wide else "card"
    return f'<section class="{cls}"><h2>{esc(title)}</h2><p class="sub">{esc(subtitle)}</p>{body}</section>'


def render(agg, history, stamp, build=""):
    t, q, d, r = agg["taches"], agg["questions"], agg["decisions"], agg["risques"]
    done = t["statuts"]["Terminé"]
    libres = t["total"] - t["en_attente"]
    status_cls = ["s0", "s1", "s2", "s3", "s4"]
    etat_cls = ["s0", "s2", "s4"]

    tiles = "".join([
        tile("Tâches terminées", done, t["total"], f"{done} sur {t['total']}"),
        tile("Tâches débloquées", libres, t["total"], f"{t['en_attente']} attendent une réponse"),
        tile("Questions tranchées", q["tranchees"], q["total"], f"{q['tranchees']} sur {q['total']}"),
        tile("Décisions actées", d["actees"], d["total"], f"{d['actees']} sur {d['total']}"),
        tile("Risques maîtrisés", r["maitrises"], r["total"], f"{r['maitrises']} sur {r['total']}"),
    ])
    cards = "".join([
        '<div class="col">',
        card(
            "Avancement par jalon", "Nombre de tâches de chaque jalon, par statut.",
            legend(STATUTS, status_cls) + responsive_bars(agg["par_jalon"], STATUTS, status_cls)
            + stacked_table(agg["par_jalon"], STATUTS), wide=True,
        ),
        card(
            "Avancement par catégorie", "Où se trouve le travail restant.",
            legend(STATUTS, status_cls) + responsive_bars(agg["par_categorie"], STATUTS, status_cls)
            + stacked_table(agg["par_categorie"], STATUTS), wide=True,
        ),
        "</div>",
        '<div class="col">',
        card(
            "Questions ouvertes par section", "Questions à trancher ou propositions à valider.",
            simple_bars(agg["par_section"]) + table(["Section", "Questions ouvertes"], agg["par_section"]),
        ),
        card(
            "Catalogue par type", "Éléments du jeu, selon leur état dans le code.",
            legend(ETATS, etat_cls) + responsive_bars(agg["par_type"], ETATS, etat_cls, wide=False)
            + stacked_table(agg["par_type"], ETATS),
        ),
        "</div>",
        '<div class="col">',
        card(
            "Matrice des risques", "Risques ouverts, selon leur probabilité et leur impact.",
            risk_matrix(agg["matrice"]),
        ),
        card(
            "Tâches terminées dans le temps", "Un relevé par jour depuis la mise en place de cette page.",
            history_chart(history),
        ),
        "</div>",
    ])
    return PAGE.replace("{{TILES}}", tiles).replace("{{CARDS}}", cards).replace(
        "{{STAMP}}", esc(stamp)).replace("{{NOTION}}", NOTION_URL).replace("{{BUILD}}", build)


PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>STAKU · Bilan</title>
<style>
:root {
  color-scheme: light;
  --page: #f6f6f4; --surface: #fcfcfb; --line: #e3e2dd; --ink: #0b0b0b; --ink2: #52514e; --ink3: #85847f;
  --s0: #d5d4ce; --s1: #86b6ef; --s2: #3987e5; --s3: #1c5cab; --s4: #0d366b;
  --l1: #cde2fb; --l2: #86b6ef; --l3: #3987e5; --l4: #1c5cab; --on-dark: #ffffff;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #121211; --surface: #1a1a19; --line: #2e2e2c; --ink: #f0efec; --ink2: #c3c2b7; --ink3: #8f8e86;
    --s0: #3d3d3a; --s1: #184f95; --s2: #256abf; --s3: #5598e7; --s4: #9ec5f4;
    --l1: #17355c; --l2: #184f95; --l3: #256abf; --l4: #5598e7; --on-dark: #ffffff;
  }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 20px 16px 32px; background: var(--page); color: var(--ink);
  font: 14px/1.45 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 1120px; margin: 0 auto; }
header { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 6px 16px;
  border-bottom: 1px solid var(--line); padding-bottom: 12px; margin-bottom: 16px; }
h1 { font-size: 13px; letter-spacing: .08em; text-transform: uppercase; margin: 0; color: var(--ink2); font-weight: 600; }
header p { margin: 0; color: var(--ink3); font-size: 12px; }
a { color: inherit; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 12px; }
.tile, .card { background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; }
.tile-label { color: var(--ink2); font-size: 12px; }
.tile-value { font-size: 30px; font-weight: 600; line-height: 1.2; font-variant-numeric: tabular-nums; }
.tile-detail { color: var(--ink3); font-size: 12px; margin-top: 6px; }
.meter { height: 6px; border-radius: 3px; background: var(--s0); overflow: hidden; margin-top: 6px; }
.meter span { display: block; height: 100%; background: var(--s2); border-radius: 3px; }
.grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.card.wide { grid-column: 1 / -1; }
.col { display: contents; }
.only-narrow, .only-fit { display: none; }
@media (max-width: 760px) {
  .grid2 { grid-template-columns: minmax(0, 1fr); }
  .only-wide { display: none; } .only-narrow { display: block; }
}
/* Vue « tout sur un écran » : la page garde une taille fixe et se met à l'échelle de la fenêtre. */
html.fit, html.fit body { height: 100%; overflow: hidden; }
html.fit body { padding: 0; }
html.fit main { position: absolute; top: 0; left: 0; width: 1280px; height: 800px; max-width: none; margin: 0;
  padding: 14px; transform-origin: 0 0; display: flex; flex-direction: column; }
html.fit header { padding-bottom: 8px; margin-bottom: 10px; }
html.fit .tiles { grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin-bottom: 10px; }
html.fit .tile { padding: 10px 14px; }
html.fit .tile-value { font-size: 26px; }
html.fit .grid2 { flex: 1; min-height: 0; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
html.fit .col { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
html.fit .card { padding: 12px; }
html.fit .col .card:last-child { flex: 1; }
html.fit h2 { margin-bottom: 8px; }
html.fit .sub, html.fit details, html.fit .only-wide, html.fit .only-narrow, html.fit .not-fit { display: none; }
html.fit .only-fit { display: block; }
html.fit .matrix { grid-template-columns: 78px repeat(3, minmax(0, 1fr)); }
html.fit .m-side { padding-left: 0; }
html.fit .note { margin: 8px 0; }
h2 { font-size: 15px; margin: 0; font-weight: 600; }
.sub { margin: 2px 0 10px; color: var(--ink3); font-size: 12px; }
svg { display: block; width: 100%; height: auto; overflow: visible; }
.lab { fill: var(--ink2); font-size: 12px; }
.val { fill: var(--ink); font-size: 12px; font-weight: 600; }
.axis { fill: var(--ink3); font-size: 11px; }
.grid { stroke: var(--line); stroke-width: 1; }
.s0 { fill: var(--s0); background: var(--s0); } .s1 { fill: var(--s1); background: var(--s1); }
.s2 { fill: var(--s2); background: var(--s2); } .s3 { fill: var(--s3); background: var(--s3); }
.s4 { fill: var(--s4); background: var(--s4); } .s-solo { fill: var(--s2); }
.line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.l-total { stroke: var(--s0); } .l-done { stroke: var(--s2); }
.dot { fill: var(--s2); stroke: var(--surface); stroke-width: 2; }
.mark { cursor: default; } .mark:hover { opacity: .8; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-bottom: 6px; color: var(--ink2); font-size: 12px; }
.key { display: inline-flex; align-items: center; gap: 6px; }
.key i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.matrix { display: grid; grid-template-columns: 120px repeat(3, minmax(0, 1fr)); gap: 2px; }
.m-head, .m-side { color: var(--ink2); font-size: 12px; padding: 4px 6px; }
.m-head { text-align: center; } .m-side { display: flex; align-items: center; }
.m-cell { min-height: 68px; border-radius: 4px; padding: 6px 8px; display: flex; flex-direction: column; gap: 2px; }
.m-cell b { font-size: 18px; font-variant-numeric: tabular-nums; } .m-cell span { font-size: 11px; }
.lvl0 { background: transparent; border: 1px dashed var(--line); }
.lvl1 { background: var(--l1); color: var(--ink); } .lvl2 { background: var(--l2); color: var(--ink); }
.lvl3 { background: var(--l3); color: var(--on-dark); } .lvl4 { background: var(--l4); color: var(--on-dark); }
@media (prefers-color-scheme: dark) { .lvl4 { color: #0b0b0b; } }
details { margin-top: 8px; color: var(--ink2); font-size: 12px; }
summary { cursor: pointer; color: var(--ink3); }
table { border-collapse: collapse; width: 100%; margin-top: 6px; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 3px 6px; border-bottom: 1px solid var(--line); font-weight: 400; }
th:first-child, td:first-child { text-align: left; } th { color: var(--ink3); }
.note { color: var(--ink3); margin: 24px 0; }
#tip { position: fixed; pointer-events: none; background: var(--ink); color: var(--surface); padding: 5px 8px;
  border-radius: 4px; font-size: 12px; max-width: 280px; opacity: 0; transition: opacity .08s; z-index: 5; }
</style>
</head>
<body>
<main>
<header>
  <h1>STAKU · Dossier patient · Bilan</h1>
  <p>Relevé automatique du {{STAMP}} · <a href="{{NOTION}}" target="_blank" rel="noopener">Ouvrir le dossier dans Notion</a></p>
</header>
<div class="tiles">{{TILES}}</div>
<div class="grid2">{{CARDS}}</div>
</main>
<div id="tip" role="status"></div>
<script>
function fit() {
  const w = window.innerWidth, h = window.innerHeight;
  const scale = Math.min(w / 1280, h / 800);
  const on = w >= 900 && scale >= 0.7;
  const main = document.querySelector('main');
  document.documentElement.classList.toggle('fit', on);
  main.style.transform = on ? 'scale(' + scale + ')' : '';
  main.style.left = on ? ((w - 1280 * scale) / 2) + 'px' : '';
}
fit();
window.addEventListener('resize', fit);
// Le navigateur garde parfois une ancienne copie de la page : on compare avec la version en ligne
// et on recharge si elle a changé (au chargement, puis toutes les 5 minutes).
const BUILD = '{{BUILD}}';
async function refresh() {
  try {
    const answer = await fetch('version.txt?' + Date.now(), { cache: 'no-store' });
    if (!answer.ok) return;
    const latest = (await answer.text()).trim();
    if (latest && latest !== BUILD && !location.search.includes('t=' + latest)) {
      location.replace(location.pathname + '?t=' + latest);
    }
  } catch (error) { /* hors ligne : on garde la page affichée */ }
}
refresh();
setInterval(refresh, 5 * 60 * 1000);
const tip = document.getElementById('tip');
document.addEventListener('pointermove', (event) => {
  const mark = event.target.closest('[data-tip]');
  if (!mark) { tip.style.opacity = 0; return; }
  tip.textContent = mark.dataset.tip;
  tip.style.opacity = 1;
  const x = Math.min(event.clientX + 12, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = Math.max(8, x) + 'px';
  tip.style.top = (event.clientY + 14) + 'px';
});
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="site")
    parser.add_argument("--sample", help="fichier JSON de test, à la place de Notion")
    args = parser.parse_args()

    if args.sample:
        with open(args.sample, encoding="utf-8") as handle:
            data = json.load(handle)
        history = data.pop("history", [])
    else:
        token = os.environ.get("NOTION_TOKEN")
        if not token:
            sys.exit("La variable NOTION_TOKEN est vide : ajoutez le secret NOTION_TOKEN au dépôt.")
        data = fetch(token)
        history = load_history(os.environ.get("SITE_URL"))

    agg = aggregate(data)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1)))
    history = update_history(history, agg, now.strftime("%Y-%m-%d"))

    build = now.strftime("%Y%m%d%H%M%S")
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(render(agg, history, now.strftime("%d/%m/%Y à %H:%M"), build))
    with open(os.path.join(args.out, "version.txt"), "w", encoding="utf-8") as handle:
        handle.write(build)
    with open(os.path.join(args.out, "history.json"), "w", encoding="utf-8") as handle:
        json.dump(history, handle, ensure_ascii=False, indent=1)
    print(f"Page écrite dans {args.out}/ : {agg['taches']['total']} tâches, "
          f"{agg['questions']['total']} questions, {len(history)} relevé(s).")


if __name__ == "__main__":
    main()
