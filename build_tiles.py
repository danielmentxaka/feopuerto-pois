#!/usr/bin/env python3
"""
Genera las casillas de POI (agua y supermercados) de Europa para FeoPuerto.

Para cada país de Geofabrik:
  1. descarga el .osm.pbf
  2. se queda sólo con los objetos candidatos (osmium tags-filter)
  3. los exporta a GeoJSON y los clasifica con los MISMOS criterios que la app
  4. borra los ficheros del país antes de pasar al siguiente (el disco del runner es pequeño)

Salida (carpeta --out, publicada tal cual en GitHub Pages):
  meta.json            versión, tamaño de casilla y lista de casillas existentes
  t/{lat}_{lon}.json   una casilla de 1°x1° (esquina suroeste), formato tipo Overpass:
                       {"elements":[{"lat":..,"lon":..,"tags":{..}}, ...]}

Uso:
  python build_tiles.py --out site                 # toda Europa
  python build_tiles.py --out site spain andorra   # sólo esas regiones (pruebas)
"""

import argparse
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.request

GEOFABRIK_INDEX = "https://download.geofabrik.de/index-v1-nogeom.json"

# Hijos de "europe" en Geofabrik que agrupan varios países: se saltan porque
# sus países ya se descargan por separado (united-kingdom = great-britain +
# ireland-and-northern-ireland).
SKIP_REGIONS = {"alps", "britain-and-ireland", "dach", "united-kingdom"}

# Candidatos para osmium: equivalen a la consulta Overpass de la app.
OSMIUM_FILTERS = [
    "nwr/amenity=drinking_water,fountain,watering_place,marketplace",
    "nwr/drinking_water=yes",
    "nwr/potable=yes",
    "nwr/man_made=water_tap,water_well",
    "nwr/natural=spring",
    "nwr/shop",
]

# Etiquetas que la app usa para clasificar y nombrar. El resto se descarta.
KEEP_TAGS = ("amenity", "shop", "brand", "name", "drinking_water",
             "potable", "man_made", "natural", "access")

KNOWN_BRANDS = ("carrefour", "eroski", "dia", "lidl", "aldi", "mercadona", "alcampo",
                "spar", "coviran", "bm", "gadis", "consum")

TILE_DEG = 1


# ----------------------------------------------------------------------------
# Clasificación (copia fiel de MainActivity.isWaterPoi / isSupermarketPoi)
# ----------------------------------------------------------------------------

def _norm(value):
    return (value or "").strip().lower()


def is_supermarket(tags):
    shop = _norm(tags.get("shop"))
    if shop in ("supermarket", "convenience", "department_store", "mall"):
        return True
    if _norm(tags.get("amenity")) == "marketplace":
        return True
    if not shop:
        return False
    text = (tags.get("brand") or "") + " " + (tags.get("name") or "")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    padded = " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "
    return any(" " + k + " " in padded for k in KNOWN_BRANDS)


def is_water(tags):
    if _norm(tags.get("amenity")) in ("drinking_water", "fountain", "watering_place"):
        return True
    if _norm(tags.get("drinking_water")) == "yes" or _norm(tags.get("potable")) == "yes":
        return True
    if _norm(tags.get("man_made")) in ("water_tap", "water_well"):
        return True
    return _norm(tags.get("natural")) == "spring"


# ----------------------------------------------------------------------------
# Geometría
# ----------------------------------------------------------------------------

def _walk_coords(coords):
    if coords and isinstance(coords[0], (int, float)):
        yield coords
    else:
        for c in coords:
            yield from _walk_coords(c)


def center_of(geometry):
    """Centro de la caja envolvente, igual que 'out center' de Overpass."""
    if not geometry:
        return None
    if geometry.get("type") == "Point":
        lon, lat = geometry["coordinates"][:2]
        return lat, lon
    min_lat = min_lon = math.inf
    max_lat = max_lon = -math.inf
    for lon, lat, *_ in _walk_coords(geometry.get("coordinates") or []):
        min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
        min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
    if min_lat is math.inf:
        return None
    return (min_lat + max_lat) / 2.0, (min_lon + max_lon) / 2.0


# ----------------------------------------------------------------------------
# Descarga y proceso de regiones
# ----------------------------------------------------------------------------

def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def europe_regions():
    with urllib.request.urlopen(GEOFABRIK_INDEX, timeout=60) as r:
        index = json.load(r)
    regions = []
    for f in index["features"]:
        p = f["properties"]
        if p.get("parent") != "europe" or p["id"] in SKIP_REGIONS:
            continue
        url = (p.get("urls") or {}).get("pbf")
        if url:
            regions.append((p["id"], url))
    regions.sort()
    return regions


def run(cmd):
    subprocess.run(cmd, check=True)


def download(url, path, attempts=4):
    for i in range(1, attempts + 1):
        try:
            run(["curl", "-fsSL", "--retry", "3", "--connect-timeout", "30", "-o", path, url])
            return
        except subprocess.CalledProcessError:
            if i == attempts:
                raise
            log(f"  descarga fallida, reintento {i}/{attempts - 1}")
            time.sleep(30 * i)


def process_region(region_id, url, work, tiles):
    pbf = os.path.join(work, "region.osm.pbf")
    filtered = os.path.join(work, "filtered.osm.pbf")
    geojson = os.path.join(work, "pois.geojsonseq")
    try:
        log(f"{region_id}: descargando")
        download(url, pbf)
        size_mb = os.path.getsize(pbf) / 1e6
        log(f"{region_id}: {size_mb:.0f} MB, filtrando")
        run(["osmium", "tags-filter", "--overwrite", "-o", filtered, pbf] + OSMIUM_FILTERS)
        os.remove(pbf)
        run(["osmium", "export", "--overwrite", "-f", "geojsonseq",
             "-x", "print_record_separator=false", "--add-unique-id=type_id",
             "-o", geojson, filtered])
        os.remove(filtered)

        kept = 0
        with open(geojson, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                feat = json.loads(line)
                tags = feat.get("properties") or {}
                water = is_water(tags)
                shop = is_supermarket(tags)
                if not water and not shop:
                    continue
                c = center_of(feat.get("geometry"))
                if c is None:
                    continue
                lat, lon = c
                small = {k: str(tags[k])[:40] for k in KEEP_TAGS if k in tags}
                key = (math.floor(lat / TILE_DEG) * TILE_DEG, math.floor(lon / TILE_DEG) * TILE_DEG)
                # El id OSM evita duplicados en las fronteras (los extractos se solapan).
                osm_id = feat.get("id") or f"{lat:.6f},{lon:.6f}"
                tiles.setdefault(key, {})[osm_id] = {"lat": round(lat, 6), "lon": round(lon, 6), "tags": small}
                kept += 1
        log(f"{region_id}: {kept} POI")
    finally:
        for p in (pbf, filtered, geojson):
            if os.path.exists(p):
                os.remove(p)


def write_site(out, tiles, regions_done):
    tile_dir = os.path.join(out, "t")
    os.makedirs(tile_dir, exist_ok=True)
    keys = []
    total = 0
    for (lat, lon), elements in sorted(tiles.items()):
        name = f"{lat}_{lon}"
        items = sorted(elements.values(), key=lambda e: (e["lat"], e["lon"]))
        with open(os.path.join(tile_dir, name + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"elements": items}, fh, ensure_ascii=False, separators=(",", ":"))
        keys.append(name)
        total += len(items)
    now = dt.datetime.now(dt.timezone.utc)
    meta = {
        "version": now.strftime("%Y%m%d%H%M"),
        "generated": now.isoformat(timespec="seconds"),
        "tileDeg": TILE_DEG,
        "pois": total,
        "regions": regions_done,
        "tiles": keys,
    }
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, separators=(",", ":"))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(f"<!doctype html><meta charset=utf-8><title>FeoPuerto POI</title>"
                 f"<p>FeoPuerto POI · {total} puntos · {len(keys)} casillas · {meta['generated']}</p>"
                 f"<p>Datos © OpenStreetMap contributors, ODbL.</p>")
    open(os.path.join(out, ".nojekyll"), "w").close()
    log(f"sitio listo: {total} POI en {len(keys)} casillas")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--work", default="work")
    ap.add_argument("regions", nargs="*", help="ids de Geofabrik (vacío = toda Europa)")
    args = ap.parse_args()

    all_regions = europe_regions()
    if args.regions:
        wanted = set(args.regions)
        selected = [r for r in all_regions if r[0] in wanted]
        missing = wanted - {r[0] for r in selected}
        if missing:
            sys.exit(f"regiones desconocidas: {', '.join(sorted(missing))}")
    else:
        selected = all_regions
    log(f"{len(selected)} regiones")

    shutil.rmtree(args.out, ignore_errors=True)
    os.makedirs(args.work, exist_ok=True)
    tiles = {}
    failed = []
    for region_id, url in selected:
        try:
            process_region(region_id, url, args.work, tiles)
        except Exception as e:  # seguimos con el resto y fallamos al final
            log(f"{region_id}: ERROR {e}")
            failed.append(region_id)

    # Si falta un país, sus casillas saldrían vacías y la app creería que allí no hay
    # nada. Mejor fallar: GitHub Pages sigue sirviendo la versión anterior completa.
    if failed:
        sys.exit(f"regiones fallidas: {', '.join(failed)}")
    write_site(args.out, tiles, [r[0] for r in selected])


if __name__ == "__main__":
    main()
