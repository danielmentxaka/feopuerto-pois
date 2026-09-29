# feopuerto-pois

Casillas de puntos de agua y supermercados de Europa para la app FeoPuerto,
generadas cada mes desde OpenStreetMap (extractos de Geofabrik) y publicadas en GitHub Pages.

- `meta.json`: versión, tamaño de casilla (1°) y lista de casillas con datos.
- `t/{lat}_{lon}.json`: POI de la casilla cuya esquina suroeste es (lat, lon).

La app sólo descarga las casillas por las que pasa la ruta y las guarda en el móvil.

## Puesta en marcha

1. Repositorio **público** (GitHub Pages gratis no funciona con repos privados).
2. Settings → Pages → Source: **GitHub Actions**.
3. Actions → *Generar casillas POI* → *Run workflow*. Para una prueba rápida pon `spain andorra`
   en *regions*; vacío = toda Europa (≈ 1 h).

Datos © OpenStreetMap contributors, licencia ODbL.
