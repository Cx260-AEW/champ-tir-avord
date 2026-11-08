#!/usr/bin/env python3
"""
Récupère le PDF officiel des fermetures du champ de tir DGA TT (Avord/Bourges)
et en extrait le statut (OUVERTE/FERMEE) de chaque itinéraire pour les 3
tranches horaires (Matin / Après-midi / Nuit), sur les jours publiés.

Écrit le résultat dans docs/data/status.json.

En cas d'échec, n'écrase JAMAIS status.json (le site garde la dernière
donnée valide) mais affiche un diagnostic détaillé dans les logs, et
sauvegarde le texte brut extrait du PDF dans /tmp/debug_pdf_text.txt
(récupéré comme artifact par le workflow GitHub Actions) pour permettre
de comprendre précisément ce qui a changé dans le document officiel.
"""
import html
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
import pdfplumber

# Le lien du PDF sur inforoute18.fr change : une URL avec token ("...&token=...")
# a cessé de fonctionner en 2 jours. Le script essaie donc plusieurs URL dans
# l'ordre et utilise la première qui donne un document valide ET à jour.
HOMEPAGE_URL = "https://www.inforoute18.fr/"
ID_VUE = "2758"
PLAIN_URL = f"https://www.inforoute18.fr/mod_turbolead/getvue.php?id_vue={ID_VUE}"
LEGACY_URL = "https://www.inforoute18.fr/mod_turbolead/getvue.php/2758_view.pdf"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "status.json"
DEBUG_TEXT_PATH = Path("/tmp/debug_pdf_text.txt")

ROUTE_LABELS = [
    ("SOYE – OSMOY CV01", "CV01"),
    ("SOYE – SAVIGNY", "ROUTE DGATT"),
    ("SAINT-JUST – SAVIGNY D46", "D46"),
    ("CROSSES – SAVIGNY D66", "D66"),
    ("CROSSES – AVORD D71", "D71"),
    ("JUSSY – AVORD D36", "D36"),
    ("RAYMOND – BAUGY D10", "D10"),
    ("CORNUSSE – BENGY D102", "D102"),
]

JOURS = r"(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)"
MOIS = (r"(?:janvier|février|mars|avril|mai|juin|juillet|août|"
        r"septembre|octobre|novembre|décembre)")
DATE_HEADING_RE = re.compile(rf"({JOURS} \d{{1,2}} {MOIS} \d{{4}})")

MOIS_NUM = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12,
}

STATUS_RE_TEMPLATE = (
    r"{label}[\s\S]{{0,150}}?(OUVERTE|FERMEE)\s+(OUVERTE|FERMEE)\s+(OUVERTE|FERMEE)"
)
# Note : pdfplumber peut parfois intercaler du texte d'une légende voisine
# (ex. l'encart carte "ROUTE DGATT") entre le libellé d'un itinéraire et ses
# statuts, à cause de l'ordre de lecture approximatif utilisé pour aplatir
# une mise en page en texte brut. Le "[\s\S]{0,150}?" tolère jusqu'à 150
# caractères de texte parasite entre le libellé et le premier statut, sans
# quoi une correspondance immédiate est de toute façon prioritaire (match
# non-gourmand).

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
}


class ScrapeError(Exception):
    """Erreur porteuse d'un contexte de diagnostic structuré."""
    def __init__(self, message, **context):
        super().__init__(message)
        self.context = context


def log_block(title, lines):
    """Affiche un bloc de diagnostic lisible dans les logs GitHub Actions."""
    print(f"\n::group::{title}", file=sys.stderr)
    for line in lines:
        print(line, file=sys.stderr)
    print("::endgroup::", file=sys.stderr)


# Motif d'un lien vers la vue du champ de tir, avec ou sans token. Le lien est
# cherché dans du texte brut (HTML, JSON, JavaScript) où il peut être échappé.
LINK_RE = re.compile(r"getvue\.php\?id_vue=" + ID_VUE + r"(?!\d)[^\"'\s<>\\]*")
# Cas où la page ne donne pas le lien complet mais id_vue et token séparément.
TOKEN_RE = re.compile(r"[0-9a-f]{32}")
VIEW_BASE = "https://www.inforoute18.fr/mod_turbolead/"


def _normalize(text: str) -> str:
    """Défait les échappements courants (JSON, HTML) pour retrouver les liens."""
    return (text.replace("\\/", "/").replace("\\u0026", "&")
                .replace("&amp;", "&").replace("&#38;", "&"))


def find_link_in_text(text: str):
    """
    Cherche dans un texte (page, réponse JSON...) le lien vers la vue du champ
    de tir. Renvoie l'URL complète ou None. Si seuls l'identifiant de vue et
    un token (32 caractères hexadécimaux) sont proches l'un de l'autre, le lien
    est reconstruit.
    """
    text = _normalize(text)
    m = LINK_RE.search(text)
    if m:
        return VIEW_BASE + m.group(0)
    for m in re.finditer(r"id_vue[\"']?\s*[:=]\s*[\"']?" + ID_VUE + r"(?!\d)", text):
        window = text[max(0, m.start() - 300): m.end() + 300]
        tok = TOKEN_RE.search(window)
        if tok:
            return f"{VIEW_BASE}getvue.php?id_vue={ID_VUE}&token={tok.group(0)}"
    return None


def discover_pdf_url():
    """Lecture simple (sans navigateur) de la page d'accueil. Rarement suffisante :
    le lien est en général construit dynamiquement. Renvoie None sinon."""
    try:
        resp = requests.get(
            HOMEPAGE_URL, timeout=30,
            headers={**HEADERS, "Accept": "text/html,*/*"},
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"[découverte] page d'accueil injoignable : {exc}", file=sys.stderr)
        return None

    url = find_link_in_text(resp.text)
    if url:
        print(f"[découverte] lien trouvé dans le code de la page : {url}", file=sys.stderr)
    else:
        print("[découverte] aucun lien dans le code statique de la page d'accueil "
              "(il est probablement construit par JavaScript)", file=sys.stderr)
    return url


DEBUG_DIR = Path("/tmp")
ICON_SELECTORS = [
    "[title*='DGA' i]", "[alt*='DGA' i]", "img[src*='dga' i]",
    "[class*='dga' i]", "[id*='dga' i]", "[data-original-title*='DGA' i]",
    "text=/\\bDGA\\b/i",
]


def ensure_chromium(p) -> None:
    """Installe Chromium à la demande (une seule fois par exécution)."""
    try:
        p.chromium.launch().close()
        return
    except Exception:
        print("[navigateur] Chromium absent, installation en cours...", file=sys.stderr)
        subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                       check=True)


def discover_with_browser():
    """
    Ouvre la page d'accueil dans un vrai navigateur sans écran, comme le ferait
    un visiteur : écoute toutes les réponses réseau et le contenu de la page à
    la recherche du lien (token compris), puis survole/clique le pictogramme DGA
    si nécessaire. Télécharge ensuite le PDF depuis le même navigateur (au cas
    où le token serait lié à la session).

    Renvoie (url, pdf_bytes_ou_None). Lève ScrapeError avec un diagnostic
    détaillé (requêtes vues, capture d'écran, HTML) si rien n'est trouvé.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ScrapeError("Playwright n'est pas installé (pip install playwright).",
                          step="découverte par navigateur")

    found = []          # (origine, url)
    requests_seen = []  # journal des requêtes pour le diagnostic

    with sync_playwright() as p:
        ensure_chromium(p)
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"], locale="fr-FR",
            viewport={"width": 1400, "height": 900},
        )
        page = ctx.new_page()

        def on_response(resp):
            try:
                kind = resp.request.resource_type
                requests_seen.append(f"{resp.status} {kind:8} {resp.url[:160]}")
                if kind in ("xhr", "fetch", "document", "script", "other"):
                    url = find_link_in_text(resp.text())
                    if url:
                        found.append((f"réponse réseau {resp.url[:80]}", url))
            except Exception:
                pass

        page.on("response", on_response)

        def scan_page(origin):
            url = find_link_in_text(page.content())
            if url:
                found.append((origin, url))
            for href in page.eval_on_selector_all(
                    "a[href*='getvue.php']", "els => els.map(e => e.href)"):
                u = find_link_in_text(href)
                if u:
                    found.append((origin + " (lien <a>)", u))

        try:
            page.goto(HOMEPAGE_URL, wait_until="networkidle", timeout=30000)
        except Exception as exc:
            requests_seen.append(f"!! chargement de la page : {exc}")
        page.wait_for_timeout(1500)
        scan_page("contenu de la page")

        # Interaction avec le pictogramme DGA (survol puis clic) si besoin.
        if not found:
            for sel in ICON_SELECTORS:
                try:
                    handles = page.locator(sel)
                    count = min(handles.count(), 4)
                except Exception:
                    continue
                for i in range(count):
                    el = handles.nth(i)
                    for action in ("hover", "click"):
                        try:
                            getattr(el, action)(timeout=3000)
                            page.wait_for_timeout(1200)
                        except Exception:
                            continue
                        scan_page(f"après {action} sur {sel}")
                        if found:
                            break
                    if found:
                        break
                if found:
                    break

        if found:
            origin, url = found[0]
            print(f"[navigateur] lien trouvé ({origin}) : {url}", file=sys.stderr)
            pdf_bytes = None
            try:
                r = ctx.request.get(url, timeout=30000)
                body = r.body()
                if r.status == 200 and body.startswith(b"%PDF"):
                    pdf_bytes = body
                else:
                    print(f"[navigateur] téléchargement via le navigateur : HTTP {r.status}",
                          file=sys.stderr)
            except Exception as exc:
                print(f"[navigateur] téléchargement via le navigateur impossible : {exc}",
                      file=sys.stderr)
            browser.close()
            return url, pdf_bytes

        # Échec : on garde de quoi comprendre ce que fait la page.
        try:
            page.screenshot(path=str(DEBUG_DIR / "debug_page.png"), full_page=True)
            (DEBUG_DIR / "debug_homepage.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        interesting = [r for r in requests_seen
                       if not re.search(r"\.(png|jpe?g|gif|webp|svg|ico|woff2?|css)(\?|$)|/tile|tiles", r, re.I)]
        (DEBUG_DIR / "debug_network.txt").write_text("\n".join(requests_seen), encoding="utf-8")
        browser.close()
        raise ScrapeError(
            "La page d'accueil a été chargée dans un navigateur mais aucun lien vers "
            "la vue du champ de tir n'a été trouvé (ni dans les réponses réseau, ni "
            "dans la page, ni après survol/clic du pictogramme DGA).",
            step="découverte par navigateur",
            details="Requêtes réseau observées (hors images/styles/tuiles) :\n  "
                    + "\n  ".join(interesting[:80])
                    + "\n(captures : debug_page.png, debug_homepage.html, debug_network.txt)",
        )


def candidate_urls():
    """Adresses simples à essayer d'abord, par ordre de priorité : (libellé, url)."""
    candidates = []
    override = os.environ.get("PDF_URL", "").strip()
    if override:
        candidates.append(("variable d'environnement PDF_URL", override))
    discovered = discover_pdf_url()
    if discovered:
        candidates.append(("lien trouvé dans le code de la page d'accueil", discovered))
    candidates.append(("URL sans token", PLAIN_URL))
    candidates.append(("ancienne URL (…/2758_view.pdf)", LEGACY_URL))

    seen, unique = set(), []
    for label, url in candidates:
        if url not in seen:
            seen.add(url)
            unique.append((label, url))
    return unique


def download_pdf(url: str) -> bytes:
    try:
        resp = requests.get(url, timeout=30, headers=HEADERS)
    except requests.exceptions.RequestException as exc:
        raise ScrapeError(
            "Impossible de joindre le serveur (problème réseau/DNS/timeout).",
            step="requête HTTP",
            url=url,
            detail=str(exc),
        )

    if resp.status_code != 200:
        raise ScrapeError(
            f"Le serveur a répondu {resp.status_code} au lieu de 200.",
            step="requête HTTP",
            url=url,
            status_code=resp.status_code,
            content_type=resp.headers.get("Content-Type"),
            body_preview=resp.text[:300] if resp.text else "(corps vide)",
        )

    if not resp.content.startswith(b"%PDF"):
        raise ScrapeError(
            "La réponse ne commence pas par la signature PDF (%PDF) — "
            "le serveur a probablement renvoyé une page HTML (redirection, "
            "page d'erreur, lien expiré) au lieu du fichier PDF attendu.",
            step="validation du contenu téléchargé",
            url=url,
            content_type=resp.headers.get("Content-Type", ""),
            content_length=len(resp.content),
            first_bytes=resp.content[:200].decode("utf-8", errors="replace"),
        )
    return resp.content


def extract_pdf_text(pdf_bytes: bytes) -> str:
    tmp_path = Path("/tmp/champ_tir.pdf")
    tmp_path.write_bytes(pdf_bytes)

    try:
        text_parts = []
        with pdfplumber.open(tmp_path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
    except Exception as exc:
        raise ScrapeError(
            "Le PDF a été téléchargé mais pdfplumber n'a pas réussi à "
            "l'ouvrir ou en extraire le texte (fichier corrompu, protégé, "
            "ou structure interne inattendue).",
            step="extraction du texte (pdfplumber)",
            file_size_bytes=len(pdf_bytes),
            detail=f"{type(exc).__name__}: {exc}",
        )

    full_text = "\n".join(text_parts)
    if not full_text.strip():
        raise ScrapeError(
            "Le PDF s'est ouvert correctement mais aucun texte n'en a été "
            "extrait (probablement un PDF scanné/image plutôt que du texte "
            "sélectionnable).",
            step="extraction du texte (pdfplumber)",
            page_count=page_count,
        )
    return full_text


def check_fresh(days: list) -> None:
    """
    Refuse un document périmé (ex. vieux PDF resservi par un lien obsolète) :
    la date la plus tardive publiée ne doit pas être antérieure à hier.
    Mieux vaut laisser le site afficher son avertissement "données anciennes"
    que présenter de vieux statuts comme frais.
    """
    latest = max(d["date"] for d in days)
    today = datetime.now(timezone.utc).date()
    limit = (today - timedelta(days=1)).isoformat()
    if latest < limit:
        raise ScrapeError(
            f"Document périmé : la date la plus récente publiée est le {latest}, "
            f"alors que nous sommes le {today.isoformat()}.",
            step="contrôle de fraîcheur",
            latest_date_in_pdf=latest,
        )


def load_status():
    """
    Obtient et valide les statuts. Étapes, dans l'ordre :
      1. adresses simples (variable PDF_URL, lien dans le code de la page
         d'accueil, anciennes adresses) ;
      2. à défaut, découverte du lien frais avec un navigateur sans écran.
    Chaque tentative passe par : téléchargement → extraction → parsing →
    contrôle de fraîcheur. Renvoie (days, url_utilisée) pour la première qui
    réussit entièrement ; sinon lève une ScrapeError qui récapitule tout.
    """
    if DEBUG_TEXT_PATH.exists():
        DEBUG_TEXT_PATH.unlink()

    attempts = []

    def attempt(label, url, prefetched=None):
        print(f"[tentative] {label} : {url}", file=sys.stderr)
        try:
            pdf_bytes = prefetched if prefetched is not None else download_pdf(url)
            text = extract_pdf_text(pdf_bytes)
            with DEBUG_TEXT_PATH.open("a", encoding="utf-8") as f:
                f.write(f"### {label} — {url}\n{text}\n\n")
            days = parse_status_text(text)
            check_fresh(days)
        except ScrapeError as exc:
            attempts.append((label, url, exc))
            print(f"[échec] {label} → {exc}", file=sys.stderr)
            return None
        print(f"[succès] {len(days)} jour(s) lus via : {label}", file=sys.stderr)
        return days

    for label, url in candidate_urls():
        days = attempt(label, url)
        if days:
            return days, url

    try:
        url, pdf_bytes = discover_with_browser()
    except ScrapeError as exc:
        attempts.append(("découverte par navigateur", "(aucun lien trouvé)", exc))
        print(f"[échec] découverte par navigateur → {exc}", file=sys.stderr)
    else:
        days = attempt("lien frais trouvé par le navigateur", url, pdf_bytes)
        if days:
            return days, url

    lines = []
    for label, url, exc in attempts:
        lines.append(f"\n- {label} : {url}")
        lines.append(f"    étape : {exc.context.get('step', 'inconnue')} — {exc}")
        for key in ("status_code", "content_type", "detail", "first_bytes",
                    "latest_date_in_pdf"):
            if key in exc.context:
                lines.append(f"    {key} : {exc.context[key]}")
        if "details" in exc.context:
            lines.append("    " + exc.context["details"].replace("\n", "\n    "))
    raise ScrapeError(
        "Aucune des méthodes n'a donné un document valide et à jour. "
        "Le lien du PDF a probablement encore changé sur inforoute18.fr.",
        step="obtention du PDF (toutes les méthodes ont échoué)",
        details="\n".join(lines),
    )


def french_date_to_iso(date_label: str) -> str:
    m = re.match(rf"{JOURS} (\d{{1,2}}) ({MOIS}) (\d{{4}})", date_label)
    if not m:
        raise ScrapeError(f"Date non reconnue lors de la conversion: {date_label}",
                           step="conversion de date")
    day, mois_str, year = m.groups()
    month = MOIS_NUM[mois_str]
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def find_route_context(block: str, route_id: str, label: str, window: int = 120) -> str:
    """
    Cherche des indices sur ce qui a changé pour un itinéraire non trouvé :
    le code de route seul (ex. 'D46') est-il présent ailleurs dans le bloc,
    avec quel texte autour ?
    """
    idx = block.find(route_id)
    if idx == -1:
        name_only = label.rsplit(" ", 1)[0] if route_id in label else label
        idx = block.find(name_only)
    if idx == -1:
        return "  → Ni le libellé complet, ni le code de route seul n'ont été trouvés dans ce bloc de date."
    start = max(0, idx - 20)
    end = min(len(block), idx + window)
    snippet = block[start:end].replace("\n", " \\n ")
    return f"  → Trouvé une correspondance partielle près de : ...{snippet}..."


def parse_status_text(text: str) -> list:
    headings = list(DATE_HEADING_RE.finditer(text))
    if not headings:
        raise ScrapeError(
            "Aucune date au format attendu ('lundi 15 septembre 2026', etc.) "
            "n'a été trouvée dans le texte extrait du PDF — la mise en page "
            "du document a probablement changé.",
            step="repérage des dates",
            text_length=len(text),
            text_preview=text[:800],
        )

    days = []
    all_missing_by_day = {}

    for i, h in enumerate(headings):
        start = h.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        block = text[start:end]

        tranches = {"matin": {}, "apres_midi": {}, "nuit": {}}
        missing = []
        for label, route_id in ROUTE_LABELS:
            pattern = re.compile(STATUS_RE_TEMPLATE.format(label=re.escape(label)))
            m = pattern.search(block)
            if not m:
                missing.append((label, route_id))
                continue
            matin, apres_midi, nuit = m.groups()
            tranches["matin"][route_id] = matin
            tranches["apres_midi"][route_id] = apres_midi
            tranches["nuit"][route_id] = nuit

        if missing:
            all_missing_by_day[h.group(1)] = (missing, block)
        else:
            days.append({
                "date": french_date_to_iso(h.group(1)),
                "label": h.group(1),
                "tranches": tranches,
            })

    if all_missing_by_day:
        diag_lines = []
        for day_label, (missing, block) in all_missing_by_day.items():
            diag_lines.append(f"\nJour '{day_label}' — {len(missing)} itinéraire(s) non reconnu(s) :")
            for label, route_id in missing:
                diag_lines.append(f" - Attendu : \"{label}\" ({route_id})")
                diag_lines.append(find_route_context(block, route_id, label))
        raise ScrapeError(
            f"{sum(len(m) for m, _ in all_missing_by_day.values())} itinéraire(s) "
            f"non trouvé(s) sur {len(all_missing_by_day)} jour(s) — le libellé exact "
            "ou l'ordre des statuts a probablement changé dans le PDF.",
            step="repérage des itinéraires et statuts",
            details="\n".join(diag_lines),
        )

    return days


def main():
    try:
        days, source_url = load_status()
    except ScrapeError as exc:
        log_block("DIAGNOSTIC — échec de la mise à jour", [
            f"Étape en échec : {exc.context.get('step', 'inconnue')}",
            f"Message : {exc}",
            "",
            *[f"{k} : {v}" for k, v in exc.context.items() if k not in ("step", "details")],
            "",
            exc.context.get("details", ""),
        ])
        print(
            "\nERREUR — le scraping du PDF a échoué (voir le détail ci-dessus). "
            "status.json n'a pas été modifié : le site continue d'afficher la "
            "dernière donnée valide.",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception:
        log_block("DIAGNOSTIC — erreur inattendue (traceback complet)", [
            traceback.format_exc()
        ])
        print(
            "\nERREUR inattendue — status.json n'a pas été modifié.",
            file=sys.stderr,
        )
        sys.exit(1)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_pdf": source_url,
        "time_slots": {
            "matin": "8h40 - 12h00",
            "apres_midi": "13h40 - 18h00",
            "nuit": "20h00 - 01h00",
        },
        "days": days,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"OK — {len(days)} jour(s) écrits dans {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
