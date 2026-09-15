#!/usr/bin/env python3
"""
Récupère le PDF officiel des fermetures du champ de tir DGA TT (Avord/Bourges)
et en extrait le statut (OUVERTE/FERMEE) de chaque itinéraire pour les 3
tranches horaires (Matin / Après-midi / Nuit), sur les jours publiés.

Écrit le résultat dans docs/data/status.json.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import pdfplumber

PDF_URL = "https://www.inforoute18.fr/mod_turbolead/getvue.php/2758_view.pdf"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "status.json"

# Correspondance entre le libellé tel qu'il apparaît dans le PDF et le
# route_id utilisé dans routes.geojson (issu du champ @id du KML).
# L'ordre compte peu ici car chaque bloc de date est traité séparément,
# mais on garde les libellés les plus longs/spécifiques en tête par prudence.
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
DATE_HEADING_RE = re.compile(
    rf"({JOURS} \d{{1,2}} {MOIS} \d{{4}})"
)

MOIS_NUM = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "décembre": 12,
}

STATUS_RE_TEMPLATE = r"{label}\s*[\n ]*\s*(OUVERTE|FERMEE)\s+(OUVERTE|FERMEE)\s+(OUVERTE|FERMEE)"


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
}


def fetch_pdf_text() -> str:
    resp = requests.get(PDF_URL, timeout=30, headers=HEADERS)
    resp.raise_for_status()
    tmp_path = Path("/tmp/champ_tir.pdf")
    tmp_path.write_bytes(resp.content)

    text_parts = []
    with pdfplumber.open(tmp_path) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def french_date_to_iso(date_label: str) -> str:
    m = re.match(rf"{JOURS} (\d{{1,2}}) ({MOIS}) (\d{{4}})", date_label)
    if not m:
        raise ValueError(f"Date non reconnue: {date_label}")
    day, mois_str, year = m.groups()
    month = MOIS_NUM[mois_str]
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def parse_status_text(text: str) -> list:
    headings = list(DATE_HEADING_RE.finditer(text))
    if not headings:
        raise ValueError("Aucune date trouvée dans le PDF — format inattendu.")

    days = []
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
                missing.append(label)
                continue
            matin, apres_midi, nuit = m.groups()
            tranches["matin"][route_id] = matin
            tranches["apres_midi"][route_id] = apres_midi
            tranches["nuit"][route_id] = nuit

        if missing:
            raise ValueError(
                f"Itinéraires non trouvés pour '{h.group(1)}': {missing}"
            )

        days.append({
            "date": french_date_to_iso(h.group(1)),
            "label": h.group(1),
            "tranches": tranches,
        })

    return days


def main():
    try:
        text = fetch_pdf_text()
        days = parse_status_text(text)
    except Exception as exc:
        print(f"ERREUR lors de la mise à jour: {exc}", file=sys.stderr)
        # On ne réécrit pas status.json en cas d'échec : le site continue
        # d'afficher la dernière donnée valide plutôt que de casser.
        sys.exit(1)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_pdf": PDF_URL,
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
