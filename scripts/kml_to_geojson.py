import json
import re
import xml.etree.ElementTree as ET

NS = {"k": "http://www.opengis.net/kml/2.2"}

def parse_coords(text):
    coords = []
    for line in text.strip().split():
        parts = line.split(",")
        lon, lat = float(parts[0]), float(parts[1])
        coords.append([lon, lat])
    return coords

def slugify(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()

def main():
    tree = ET.parse("/mnt/user-data/uploads/Champ_de_Tir_DGA_TT.kml")
    root = tree.getroot()
    features = []

    for pm in root.iter("{http://www.opengis.net/kml/2.2}Placemark"):
        name_el = pm.find("k:name", NS)
        name = name_el.text.strip() if name_el is not None else None

        route_id = None
        for data in pm.findall(".//k:Data", NS):
            if data.get("name") == "@id":
                val = data.find("k:value", NS)
                route_id = val.text.strip() if val is not None and val.text else None

        poly = pm.find(".//k:Polygon", NS)
        line = pm.find(".//k:LineString", NS)

        if poly is not None:
            coords_el = poly.find(".//k:coordinates", NS)
            coords = parse_coords(coords_el.text)
            geom = {"type": "Polygon", "coordinates": [coords]}
            props = {"name": name, "kind": "zone"}
            features.append({"type": "Feature", "geometry": geom, "properties": props})
        elif line is not None:
            coords_el = line.find(".//k:coordinates", NS)
            coords = parse_coords(coords_el.text)
            geom = {"type": "LineString", "coordinates": coords}
            if route_id:
                # Itinéraire avec un code (@id) : suivi dans le PDF officiel,
                # coloré selon son statut ouvert/fermé.
                props = {"name": name, "route_id": route_id, "kind": "route"}
            else:
                # Pas de code dans le KML : route d'accès non gérée par le
                # PDF (ex. "ROUTE ACCES CROSSES"). Toujours affichée en noir,
                # jamais colorée par un statut.
                props = {"name": name, "route_id": slugify(name), "kind": "access_route"}
            features.append({"type": "Feature", "geometry": geom, "properties": props})

    fc = {"type": "FeatureCollection", "features": features}
    with open("docs/data/routes.geojson", "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=2)

    print(f"{len(features)} features écrites.")
    for f in features:
        print(" -", f["properties"].get("name"), "|", f["properties"].get("route_id"), "|", f["properties"]["kind"])

if __name__ == "__main__":
    main()