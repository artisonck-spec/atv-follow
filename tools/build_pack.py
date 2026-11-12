# Builds the Latvia map data pack for the ATV Trails app: places along the way (fuel, food, service...),
# trail conditions (water crossings, mud, sand, rough, overgrown) and places to discover.
# Runs every week on GitHub Actions; the app downloads data/latvia.json once and then keeps it fresh.
# Keep the filters in step with POI, challengeType() and funQuery() in the app's index.html.
import json, sys, time, os, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

S, W, N, E = 55.60, 20.80, 58.15, 28.30          # Latvia plus a strip over each border
ROWS, COLS = 2, 4
HOSTS = ['https://overpass-api.de', 'https://overpass.kumi.systems', 'https://overpass.private.coffee']
UA = {'User-Agent': 'ATV-Trails-pack/1.0 (github.com/artisonck-spec/atv-follow)'}

POI_Q = ['amenity=fuel', 'amenity=restaurant', 'amenity=cafe', 'amenity=fast_food', 'amenity=pub',
         'shop=car_repair', 'shop=motorcycle', 'shop=tyres', 'shop=car_parts', 'craft=mechanic',
         'shop=supermarket', 'shop=convenience', 'amenity=parking', 'tourism=camp_site', 'tourism=caravan_site',
         'amenity=shelter', 'tourism=viewpoint', 'amenity=toilets', 'amenity=drinking_water',
         'amenity=hospital', 'amenity=clinic', 'amenity=pharmacy',
         'tourism=hotel', 'tourism=guest_house', 'tourism=motel', 'tourism=chalet',
         'sport=motocross', 'sport=atv', 'sport=4wd', 'sport=enduro', 'sport=motor', 'highway=raceway']   # play zones
COND_Q = ['way["highway"~"^(track|path|unclassified)$"]["surface"="mud"]',
          'way["highway"~"^(track|path)$"]["smoothness"~"^(very_bad|horrible|very_horrible|impassable)$"]',
          'way["highway"="track"]["tracktype"="grade5"]', 'nwr["ford"~"^(yes|stepping_stones)$"]',
          'way["highway"~"^(track|path)$"]["wetland"]', 'way["highway"~"^(track|path)$"]["surface"="sand"]',
          'way["highway"~"^(track|path)$"]["overgrown"]']
FUN_Q = ['nwr["tourism"="viewpoint"]', 'nwr["man_made"="tower"]["tower:type"="observation"]', 'nwr["tourism"="attraction"]',
         'node["natural"="peak"]["name"]', 'nwr["military"="bunker"]', 'nwr["building"="bunker"]',
         'nwr["historic"~"^(fort|castle|ruins|manor|archaeological_site|bunker|battlefield|military|tank|aircraft|city_gate|tower)$"]',
         'nwr["natural"~"^(waterfall|cave_entrance)$"]', 'nwr["waterway"="waterfall"]', 'node["natural"~"^(stone|spring)$"]["name"]']

POI_KEEP = {'name', 'brand', 'opening_hours', 'amenity', 'shop', 'tourism', 'craft', 'sport', 'highway'}
FUN_DROP = ('source', 'addr:', 'note', 'fixme', 'FIXME', 'check_date', 'ref:', 'created_by', 'survey', 'mapillary', 'contact:', 'phone', 'email', 'website')


class Busy(Exception):
    pass


def overpass(q, rounds=3):
    for attempt in range(rounds):
        for h in HOSTS:
            try:
                req = urllib.request.Request(h + '/api/interpreter', data=('data=' + urllib.parse.quote(q)).encode(), headers=UA)
                with urllib.request.urlopen(req, timeout=300) as r:
                    j = json.loads(r.read())
                if j.get('remark') and 'error' in j['remark'].lower():
                    raise RuntimeError(j['remark'][:200])
                return j.get('elements', [])
            except Exception as e:
                print('  ', h, 'failed:', str(e)[:160], flush=True)
                time.sleep(15)
        time.sleep(45)
    raise Busy()


def split4(box):
    s, w, n, e = box; ml, mo = (s + n) / 2, (w + e) / 2
    return [(s, w, ml, mo), (s, mo, ml, e), (ml, w, n, mo), (ml, mo, n, e)]


def fetch_box(make_query, box, depth=0):
    """One region. If the servers cannot manage it, it is cut into four smaller pieces (up to three times) and tried again."""
    bb = '(%.3f,%.3f,%.3f,%.3f)' % box
    try:
        return overpass(make_query(bb), rounds=3 if depth >= 3 else 1)
    except Busy:
        if depth >= 3:
            raise SystemExit('Overpass kept failing; the previous pack stays published')
        print('   region too heavy for the servers right now, cutting it in four:', bb, flush=True)
        out = []
        for sub in split4(box):
            out += fetch_box(make_query, sub, depth + 1); time.sleep(5)
        return out


def tiles():
    dl, dw = (N - S) / ROWS, (E - W) / COLS
    for r in range(ROWS):
        for c in range(COLS):
            yield (S + r * dl, W + c * dw, S + (r + 1) * dl, W + (c + 1) * dw)


def collect(name, filters):
    out = {}
    for box in tiles():
        els = fetch_box(lambda bb: '[out:json][timeout:240][maxsize:536870912];(' + ''.join(f + bb + ';' for f in filters) + ');out center tags;', box)
        for e in els:
            out[e['type'][0] + str(e['id'])] = e
        print(f'{name} {"(%.3f,%.3f,%.3f,%.3f)" % box}: {len(els)} (total {len(out)})', flush=True)
        time.sleep(5)
    return out


def ll(e):
    lat = e.get('lat', (e.get('center') or {}).get('lat'))
    lon = e.get('lon', (e.get('center') or {}).get('lon'))
    return (round(lat, 5), round(lon, 5)) if lat is not None else None


def cond_type(t):   # same as challengeType() in the app
    if t.get('ford'): return 'water'
    if t.get('surface') == 'mud' or t.get('wetland'): return 'mud'
    if t.get('surface') == 'sand': return 'sand'
    s = t.get('smoothness', '')
    if 'horrible' in s or 'impassable' in s or 'very_bad' in s: return 'rough'
    if t.get('tracktype') == 'grade5' or (t.get('overgrown') and t.get('overgrown') != 'no'): return 'overgrown'
    return ''


pois_raw = collect('places', [f'nwr["{k}"="{v}"]' for k, v in (f.split('=') for f in POI_Q)])
cond_raw = collect('conditions', COND_Q)
fun_raw = collect('discover', FUN_Q)

pois = []
for e in pois_raw.values():
    p, t = ll(e), e.get('tags', {})
    if p: pois.append([p[0], p[1], {k: v for k, v in t.items() if k in POI_KEEP}])
cond = []
for e in cond_raw.values():
    p, t = ll(e), e.get('tags', {})
    k = cond_type(t)
    if p and k and not t.get('amenity') and not t.get('shop') and not t.get('tourism'):
        cond.append([p[0], p[1], k[0]])
fun = []
for key, e in fun_raw.items():
    p, t = ll(e), e.get('tags', {})
    if p: fun.append([key, p[0], p[1], {k: v for k, v in t.items() if not k.startswith(FUN_DROP) and not (k.startswith('name:') and k != 'name:en')}])

# climbs from ground height (a failure here never blocks the rest of the pack)
climbs = []
try:
    import climbs as climbfinder
    climbs = climbfinder.find((S, W, N, E))
except Exception as e:
    print('climbs skipped:', e)
cond += climbs
counts = {'places': len(pois), 'conditions': len(cond), 'discover': len(fun), 'climbs': len(climbs)}
print('counts', counts)
# Sanity check so a half-broken run never replaces a good pack
if counts['places'] < 3000 or counts['conditions'] < 1000 or counts['discover'] < 500:
    raise SystemExit('Too little data, keeping the previous pack')

t = int(time.time() * 1000)
os.makedirs('data', exist_ok=True)
body = json.dumps({'v': 1, 't': t, 'bbox': [S, W, N, E], 'pois': pois, 'cond': cond, 'fun': fun}, ensure_ascii=False, separators=(',', ':'))
open('data/latvia.json', 'w').write(body)
size = len(body.encode())
json.dump({'v': 1, 't': t, 'bytes': size, 'bbox': [S, W, N, E], 'counts': counts}, open('data/latvia-meta.json', 'w'))
import gzip
print(f'latvia.json {size / 1e6:.1f} MB, about {len(gzip.compress(body.encode())) / 1e6:.1f} MB to download')
