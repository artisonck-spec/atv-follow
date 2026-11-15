"""Official protected nature areas of Latvia (Dabas aizsardzības pārvalde, data.gov.lv, updated once a year).

Each area gets a kind:
  'strict' – riding off public roads is not allowed: nature reserves (and their nature-reserve, strict and regulated zones),
             strict reserves, microreserves. Trail routes go round these.
  'park'   – national parks, nature parks, protected landscapes and their milder zones: shown on the map,
             with a "stay on roads" note, but routes are not blocked (they are very large).
Left out: nature monuments (avenues, trees, boulders), sea areas, microreserve buffer zones and neutral zones (villages, farmland).
"""
import io, zipfile, urllib.request
import shapefile
from pyproj import Transformer
from shapely.geometry import shape

IADT = 'https://data.gov.lv/dati/dataset/b2de2313-c20e-4ebf-a9c0-b8099566320a/resource/aa165932-8835-41a0-baa2-3336d2e8e6ef/download/ipasi_aizsargajamas_dabas_teritorijas.zip'
MIKRO = 'https://data.gov.lv/dati/dataset/fa50b0fb-04f2-452f-ade9-f8448d94eefa/resource/4770cc11-02eb-42e1-98c9-e640e3d8b7ff/download/mikroliegumi.zip'
TO_WGS = Transformer.from_crs('EPSG:3059', 'EPSG:4326', always_xy=True)

BASE_KIND = {'DL': 'strict', 'DR': 'strict', 'ML': 'strict', 'NP': 'park', 'DP': 'park', 'AAA': 'park', 'BR': 'park'}
ZONE_KIND = {'DLZ': 'strict', 'SLZ': 'strict', 'SRZ': 'strict', 'RRZ': 'strict', 'DPZ': 'park', 'AAZ': 'park', 'KvZ': 'park'}
ZONE_NAME = {'DLZ': 'nature reserve zone', 'SLZ': 'strict zone', 'SRZ': 'strict zone', 'RRZ': 'regulated zone', 'DPZ': 'nature park zone', 'AAZ': 'landscape zone', 'KvZ': 'cultural zone'}


def _zip(url):
    b = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ATV-Trails map data'}), timeout=180).read()
    return zipfile.ZipFile(io.BytesIO(b))


def _layer(z, name):
    n = next(x for x in z.namelist() if x.lower().endswith('/' + name.lower() + '.shp') or x.lower() == name.lower() + '.shp')
    base = n[:-4]
    return shapefile.Reader(shp=io.BytesIO(z.read(n)), dbf=io.BytesIO(z.read(base + '.dbf')), encoding='utf-8', encodingErrors='replace')


def _rings(geom, tol):
    """Outer rings in [lat, lon], simplified; small bits dropped."""
    g = shape(geom)
    polys = list(g.geoms) if g.geom_type == 'MultiPolygon' else [g] if g.geom_type == 'Polygon' else []
    out = []
    for p in polys:
        if p.area < 3000: continue                 # under 0.3 ha
        s = p.simplify(tol, preserve_topology=True)
        if s.is_empty or s.geom_type != 'Polygon': continue
        pts = []
        for x, y in list(s.exterior.coords)[:-1]:
            lon, lat = TO_WGS.transform(x, y)
            pts.append([round(lat, 5), round(lon, 5)])
        if len(pts) >= 3: out.append(pts)
    return out


def load():
    """[kind, name, ring] for every area (a multi-part area gives several)."""
    out = []
    z = _zip(IADT)
    base = _layer(z, 'IADT_pamatteritorijas')
    for sr in base.iterShapeRecords():
        r = sr.record.as_dict()
        if r.get('STATUS') != 1: continue
        k = BASE_KIND.get(r.get('CATEGORY'))
        if not k: continue
        # a zoned area is drawn by its zones; the outline alone is kept only for the big parks (shown, not blocked)
        if k == 'strict' and 'funkcionālajās zonās' not in (r.get('ATIS_veids') or '') and r.get('CATEGORY') == 'DL': continue
        for ring in _rings(sr.shape.__geo_interface__, 20 if k == 'strict' else 60):
            out.append([k, (r.get('NAME') or '')[:60], ring])
    zones = _layer(z, 'IADT_zonejums')
    for sr in zones.iterShapeRecords():
        r = sr.record.as_dict()
        if r.get('STATUS') != 1: continue
        code = r.get('NAME'); k = ZONE_KIND.get(code)
        if not k: continue
        nm = ((r.get('SITE_NAME') or '') + ' · ' + ZONE_NAME[code]).strip(' ·')[:60]
        for ring in _rings(sr.shape.__geo_interface__, 20 if k == 'strict' else 60):
            out.append([k, nm, ring])
    m = _layer(_zip(MIKRO), 'Mikroliegumi')
    for sr in m.iterShapeRecords():
        for ring in _rings(sr.shape.__geo_interface__, 15):
            out.append(['micro', 'Microreserve', ring])
    return out


if __name__ == '__main__':
    a = load()
    import collections
    print(collections.Counter(x[0] for x in a), sum(len(x[2]) for x in a), 'points')
