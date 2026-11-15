# Builds the Latvia map data pack for the ATV Trails app: places along the way (fuel, food, service, play zones...),
# trail conditions (water crossings, mud, sand, rough, overgrown, climbs) and places to discover.
# Runs every week on GitHub Actions; the app downloads data/latvia.json once and then keeps it fresh.
#
# The data is read from the daily OpenStreetMap country files of Geofabrik (Latvia, plus Estonia and Lithuania for
# the strip just over the border). Earlier this asked the free Overpass servers region by region, which failed for
# hours whenever they were busy; a plain file download does not.
# Keep the filters in step with POI, challengeType() and funQuery() in the app's index.html.
import json, sys, time, os, gzip, urllib.request
import numpy as np
import osmium
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The build machines sometimes have no working IPv6 route ("Network is unreachable"): always connect over IPv4.
import socket
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, family=0, *a, **k: _gai(host, port, socket.AF_INET, *a, **k)

S, W, N, E = 55.60, 20.80, 58.15, 28.30          # Latvia plus a strip over each border
FILES = ['latvia', 'estonia', 'lithuania']
UA = {'User-Agent': 'ATV-Trails-pack/1.0 (github.com/artisonck-spec/atv-follow)'}

POI_Q = {tuple(f.split('=')) for f in [
    'amenity=fuel', 'amenity=restaurant', 'amenity=cafe', 'amenity=fast_food', 'amenity=pub',
    'shop=car_repair', 'shop=motorcycle', 'shop=tyres', 'shop=car_parts', 'craft=mechanic',
    'shop=supermarket', 'shop=convenience', 'amenity=parking', 'tourism=camp_site', 'tourism=caravan_site',
    'amenity=shelter', 'tourism=viewpoint', 'amenity=toilets', 'amenity=drinking_water',
    'amenity=hospital', 'amenity=clinic', 'amenity=pharmacy',
    'tourism=hotel', 'tourism=guest_house', 'tourism=motel', 'tourism=chalet',
    'sport=motocross', 'sport=atv', 'sport=4wd', 'sport=enduro', 'sport=motor', 'highway=raceway']}   # the last row: play zones
POI_KEYS = {k for k, _ in POI_Q}
POI_KEEP = {'name', 'brand', 'opening_hours', 'amenity', 'shop', 'tourism', 'craft', 'sport', 'highway'}
FUN_DROP = ('source', 'addr:', 'note', 'fixme', 'FIXME', 'check_date', 'ref:', 'created_by', 'survey', 'mapillary', 'contact:', 'phone', 'email', 'website')
HISTORIC = {'fort', 'castle', 'ruins', 'manor', 'archaeological_site', 'bunker', 'battlefield', 'military', 'tank', 'aircraft', 'city_gate', 'tower'}
ROUGH = {'very_bad', 'horrible', 'very_horrible', 'impassable'}


def is_poi(t):
    for k in POI_KEYS:
        v = t.get(k)
        if v is not None and (k, v) in POI_Q: return True
    return False


def is_fun(t, node):
    return (t.get('tourism') in ('viewpoint', 'attraction') or (t.get('man_made') == 'tower' and t.get('tower:type') == 'observation')
            or t.get('military') == 'bunker' or t.get('building') == 'bunker' or t.get('historic') in HISTORIC
            or t.get('natural') in ('waterfall', 'cave_entrance') or t.get('waterway') == 'waterfall'
            or (node and t.get('name') and t.get('natural') in ('peak', 'stone', 'spring')))


def is_cond(t, way):
    if t.get('ford') in ('yes', 'stepping_stones'): return True
    if not way: return False
    hw = t.get('highway')
    if hw in ('track', 'path', 'unclassified') and t.get('surface') == 'mud': return True
    if hw == 'track' and t.get('tracktype') == 'grade5': return True
    if hw in ('track', 'path'):
        return t.get('smoothness') in ROUGH or 'wetland' in t or t.get('surface') == 'sand' or 'overgrown' in t
    return False


def cond_type(t):   # same as challengeType() in the app
    if t.get('ford'): return 'water'
    if t.get('surface') == 'mud' or t.get('wetland'): return 'mud'
    if t.get('surface') == 'sand': return 'sand'
    s = t.get('smoothness', '')
    if 'horrible' in s or 'impassable' in s or 'very_bad' in s: return 'rough'
    if t.get('tracktype') == 'grade5' or (t.get('overgrown') and t.get('overgrown') != 'no'): return 'overgrown'
    return ''


pois, cond, fun, tracks = {}, {}, {}, {}
nogo = {}   # areas where riding off public roads is not allowed: id -> [kind, name, [[lat, lon], ...]]


def nogo_kind(t):
    """Nature reserves (not the big national or landscape parks, which have their own zones) and military areas."""
    pc = t.get('protect_class', '')
    if t.get('landuse') == 'military' or t.get('military') in ('danger_area', 'training_area', 'range'): return 'military'
    if pc in ('2', '5', '6'): return ''
    if t.get('leisure') == 'nature_reserve' or (t.get('boundary') == 'protected_area' and pc in ('1', '1a', '1b', '4')): return 'reserve'
    return ''


class NoGo(osmium.SimpleHandler):
    def area(self, a):
        t = {x.k: x.v for x in a.tags}
        k = nogo_kind(t)
        if not k: return
        try:
            from shapely.geometry import Polygon
            best = None
            for ring in a.outer_rings():
                pts = [(n.lon, n.lat) for n in ring]
                if len(pts) < 4: continue
                p = Polygon(pts)
                if best is None or p.area > best.area: best = p
            if best is None: return
            c = best.centroid
            if not (S <= c.y <= N and W <= c.x <= E): return
            km2 = best.area * 111.3 * 111.3 * 0.55   # rough, at Latvia's latitude
            if km2 < 0.03: return                        # smaller than 3 ha: leave out
            g = best.simplify(0.0004, preserve_topology=True)
            if g.geom_type != 'Polygon' or g.is_empty: return
            ring = [[round(y, 4), round(x, 4)] for x, y in list(g.exterior.coords)[:-1]]
            if len(ring) < 3: return
            nogo[('w' if a.from_way() else 'r') + str(a.orig_id())] = [k, t.get('name', '')[:60], ring]
        except Exception:
            pass

cams = {}   # speed cameras: [lat, lon, limit km/h or 0, facing direction in degrees or -1]
sections = {}   # average-speed sections: relation id -> {'from': node id, 'to': node id, 'lim': km/h}
want_nodes = {}  # node id -> [lat, lon], filled in a second, quick pass


class Reader(osmium.SimpleHandler):
    def take(self, key, lat, lon, t, node):
        if not (S <= lat <= N and W <= lon <= E): return
        lat, lon = round(lat, 5), round(lon, 5)
        if is_poi(t): pois[key] = [lat, lon, {k: v for k, v in t.items() if k in POI_KEEP}]
        if is_cond(t, not node):
            k = cond_type(t)
            if k and not t.get('amenity') and not t.get('shop') and not t.get('tourism'): cond[key] = [lat, lon, k[0]]
        if is_fun(t, node):
            fun[key] = [key, lat, lon, {k: v for k, v in t.items() if not k.startswith(FUN_DROP) and not (k.startswith('name:') and k != 'name:en')}]

    def node(self, n):
        if len(n.tags) == 0: return
        t = {x.k: x.v for x in n.tags}
        if t.get('highway') == 'speed_camera' or t.get('enforcement') == 'maxspeed':
            try:
                lat, lon = n.location.lat, n.location.lon
                if S <= lat <= N and W <= lon <= E:
                    lim = t.get('maxspeed', '').split()[0] if t.get('maxspeed') else ''
                    d = t.get('direction', '')
                    deg = {'N': 0, 'NE': 45, 'E': 90, 'SE': 135, 'S': 180, 'SW': 225, 'W': 270, 'NW': 315}.get(d.upper(), None)
                    if deg is None:
                        try: deg = int(float(d)) % 360
                        except ValueError: deg = -1
                    cams['n%d' % n.id] = [round(lat, 5), round(lon, 5), int(lim) if lim.isdigit() else 0, deg]
            except Exception: pass
        try: self.take('n%d' % n.id, n.location.lat, n.location.lon, t, True)
        except Exception: pass

    def relation(self, r):
        t = {x.k: x.v for x in r.tags}
        if t.get('type') != 'enforcement' or t.get('enforcement') not in ('average_speed', 'section_control'): return
        fr = [m.ref for m in r.members if m.type == 'n' and m.role in ('from', 'start')]
        to = [m.ref for m in r.members if m.type == 'n' and m.role in ('to', 'end')]
        if not fr or not to: return
        lim = (t.get('maxspeed') or '').split()[0] if t.get('maxspeed') else ''
        sections['r%d' % r.id] = {'from': fr[0], 'to': to[0], 'lim': int(lim) if lim.isdigit() else 0}
        want_nodes[fr[0]] = None; want_nodes[to[0]] = None

    def way(self, w):
        if len(w.tags) == 0: return
        t = {x.k: x.v for x in w.tags}
        hw = t.get('highway')
        track = hw in ('track', 'path') and t.get('access') not in ('no', 'private') and t.get('motor_vehicle') not in ('no', 'private')
        if not (track or is_poi(t) or is_cond(t, True) or is_fun(t, False)): return
        try: g = [(nd.lat, nd.lon) for nd in w.nodes]
        except Exception: return          # a way reaching outside the country file
        if len(g) < 2: return
        mid = g[len(g) // 2] if hw else (sum(p[0] for p in g) / len(g), sum(p[1] for p in g) / len(g))   # tracks: a point on the track itself
        self.take('w%d' % w.id, mid[0], mid[1], t, False)
        if track and S <= mid[0] <= N and W <= mid[1] <= E: tracks['w%d' % w.id] = np.array(g, dtype=np.float64)


os.makedirs('/tmp/osm', exist_ok=True)
for name in FILES:
    path = '/tmp/osm/%s.osm.pbf' % name
    url = 'https://download.geofabrik.de/europe/%s-latest.osm.pbf' % name
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r, open(path, 'wb') as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk: break
                    f.write(chunk)
            break
        except Exception as e:
            print(name, 'download failed:', str(e)[:120], flush=True); time.sleep(30)
    else:
        raise SystemExit('Could not download the map file for ' + name + '; the previous pack stays published')
    t0 = time.time()
    Reader().apply_file(path, locations=True)
    if any(v is None for v in want_nodes.values()):   # where average-speed sections start and end
        class Nodes(osmium.SimpleHandler):
            def node(self, n):
                if n.id in want_nodes and want_nodes[n.id] is None: want_nodes[n.id] = [round(n.location.lat, 5), round(n.location.lon, 5)]
        Nodes().apply_file(path)
    try: NoGo().apply_file(path, locations=True)
    except Exception as e: print('no-go areas skipped for', name, repr(e)[:200])
    os.remove(path)
    print(f'{name}: read in {time.time() - t0:.0f} s · places {len(pois)} conditions {len(cond)} discover {len(fun)} tracks {len(tracks)}', flush=True)

pois, cond, fun = list(pois.values()), list(cond.values()), list(fun.values())

# climbs from ground height (a failure here never blocks the rest of the pack)
climbs = []
try:
    import climbs as climbfinder
    climbs = climbfinder.from_ways(list(tracks.values()))
except Exception as e:
    print('climbs skipped:', repr(e)[:300])
cond += climbs
secs = []
for k, v in sections.items():
    a, b = want_nodes.get(v['from']), want_nodes.get(v['to'])
    if a and b and S <= a[0] <= N and W <= a[1] <= E: secs.append([a[0], a[1], b[0], b[1], v['lim']])
counts = {'nogo': len(nogo), 'places': len(pois), 'conditions': len(cond), 'discover': len(fun), 'climbs': len(climbs), 'cameras': len(cams), 'avg_sections': len(secs)}
print('counts', counts)
# Sanity check so a half-broken run never replaces a good pack
if counts['places'] < 3000 or counts['conditions'] < 1000 or counts['discover'] < 500:
    raise SystemExit('Too little data, keeping the previous pack')

t = int(time.time() * 1000)
os.makedirs('data', exist_ok=True)
body = json.dumps({'v': 1, 't': t, 'bbox': [S, W, N, E], 'pois': pois, 'cond': cond, 'fun': fun, 'cams': list(cams.values()), 'secs': secs, 'nogo': list(nogo.values())}, ensure_ascii=False, separators=(',', ':'))
open('data/latvia.json', 'w').write(body)
size = len(body.encode())
json.dump({'v': 1, 't': t, 'bytes': size, 'bbox': [S, W, N, E], 'counts': counts}, open('data/latvia-meta.json', 'w'))
print(f'latvia.json {size / 1e6:.1f} MB, about {len(gzip.compress(body.encode())) / 1e6:.1f} MB to download')
