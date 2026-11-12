# Short steep climbs on forest tracks and paths: ground height from the free AWS Terrain Tiles (terrarium),
# sampled every 10 m along each track. A climb = at least 6 m up (or down) at 7 % or steeper over 30-200 m.
import io, json, math, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image

Z = 12   # ~20 m pixels here; the height data underneath is ~30 m, so more zoom adds nothing
UA = {'User-Agent': 'ATV-Trails-pack/1.0 (github.com/artisonck-spec/atv-follow)'}
HOSTS = ['https://overpass-api.de', 'https://overpass.kumi.systems', 'https://overpass.private.coffee']


def _get(url, data=None, timeout=300):
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=timeout).read()


def _overpass(q, rounds=3):
    for attempt in range(rounds):
        for h in HOSTS:
            try:
                return json.loads(_get(h + '/api/interpreter', ('data=' + urllib.parse.quote(q)).encode()))['elements']
            except Exception as e:
                print('  ', h, 'failed:', str(e)[:120], flush=True); time.sleep(15)
        time.sleep(45)
    raise RuntimeError('overpass kept failing')


def _xy(lat, lon):
    n = 2 ** Z
    return (lon + 180) / 360 * n, (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n


def _tile(key):
    x, y = key
    for i in range(4):
        try:
            a = np.asarray(Image.open(io.BytesIO(_get(f'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{Z}/{x}/{y}.png', timeout=60))).convert('RGB'), dtype=np.float32)
            return key, a[:, :, 0] * 256 + a[:, :, 1] + a[:, :, 2] / 256 - 32768
        except Exception:
            time.sleep(3 * (i + 1))
    return key, None


def find(bbox, rows=4, cols=8):
    S, W, N, E = bbox
    ways = []
    dl, dw = (N - S) / rows, (E - W) / cols
    def one(box, depth=0):
        bb = '(%.3f,%.3f,%.3f,%.3f)' % box
        q = ('[out:json][timeout:240][maxsize:1073741824];way["highway"~"^(track|path)$"]["access"!~"^(no|private)$"]'
             f'["motor_vehicle"!~"^(no|private)$"]{bb};out skel geom;')
        try:
            return _overpass(q, rounds=3 if depth >= 3 else 1)
        except RuntimeError:
            if depth >= 3: raise
            print('   climbs: region too heavy right now, cutting it in four:', bb, flush=True)
            s2, w2, n2, e2 = box; ml, mo = (s2 + n2) / 2, (w2 + e2) / 2
            out = []
            for sub in [(s2, w2, ml, mo), (s2, mo, ml, e2), (ml, w2, n2, mo), (ml, mo, n2, e2)]:
                out += one(sub, depth + 1); time.sleep(4)
            return out
    for r in range(rows):
        for c in range(cols):
            box = (S + r * dl, W + c * dw, S + (r + 1) * dl, W + (c + 1) * dw)
            els = one(box)
            ways += [[(p['lat'], p['lon']) for p in e.get('geometry', [])] for e in els if e.get('geometry')]
            print(f'climbs: tracks {"(%.3f,%.3f,%.3f,%.3f)" % box} {len(els)} (total {len(ways)})', flush=True)
            time.sleep(4)
    return from_ways(ways)


def from_ways(ways):
    """ways: list of [(lat, lon), ...] (or Nx2 arrays). Returns climb spots [lat, lon, 'c', gain m, grade %, length m]."""
    # resample every 10 m
    lines = []
    for g in ways:
        g = [tuple(x) for x in g]
        pts = [g[0]]
        for a, b in zip(g, g[1:]):
            d = math.hypot((b[0] - a[0]) * 111320, (b[1] - a[1]) * 111320 * math.cos(math.radians(a[0])))
            k = int(d // 10)
            for i in range(1, k + 1): pts.append((a[0] + (b[0] - a[0]) * i * 10 / d, a[1] + (b[1] - a[1]) * i * 10 / d))
            if pts[-1] != b: pts.append(b)
        if len(pts) >= 4: lines.append(np.array(pts))
    keys = set()
    for p in lines:
        n = 2 ** Z
        xs = (p[:, 1] + 180) / 360 * n
        ys = (1 - np.log(np.tan(np.radians(p[:, 0])) + 1 / np.cos(np.radians(p[:, 0]))) / math.pi) / 2 * n
        keys.update(zip(xs.astype(int).tolist(), ys.astype(int).tolist()))
    print('climbs: height tiles to fetch', len(keys), flush=True)
    with ThreadPoolExecutor(16) as ex:
        tiles = dict(ex.map(_tile, keys))
    print('climbs: tiles missing', sum(v is None for v in tiles.values()), flush=True)
    out = []
    for p in lines:
        n = 2 ** Z
        xs = (p[:, 1] + 180) / 360 * n
        ys = (1 - np.log(np.tan(np.radians(p[:, 0])) + 1 / np.cos(np.radians(p[:, 0]))) / math.pi) / 2 * n
        hs = np.empty(len(p)); ok = True
        for i in range(len(p)):
            tx, ty = int(xs[i]), int(ys[i]); t = tiles.get((tx, ty))
            if t is None: ok = False; break
            fx, fy = (xs[i] - tx) * 256 - .5, (ys[i] - ty) * 256 - .5
            ix, iy = min(254, max(0, int(fx))), min(254, max(0, int(fy))); ax, ay = min(1, max(0, fx - ix)), min(1, max(0, fy - iy))
            hs[i] = (t[iy, ix] * (1 - ax) + t[iy, ix + 1] * ax) * (1 - ay) + (t[iy + 1, ix] * (1 - ax) + t[iy + 1, ix + 1] * ax) * ay
        if not ok: continue
        hs = np.convolve(np.pad(hs, 1, mode='edge'), np.ones(3) / 3, mode='valid')   # light smoothing
        # best climb in every ~400 m stretch of the track
        best = {}
        for k in range(3, 21):
            if len(hs) <= k: break
            gain = np.abs(hs[k:] - hs[:-k]); grade = gain / (k * 10)
            good = np.where((gain >= 6) & (grade >= .07))[0]
            for i in good:
                m = i + k // 2; chunk = m // 40; score = gain[i] * grade[i]
                if chunk not in best or score > best[chunk][0]:
                    best[chunk] = (score, float(gain[i]), float(grade[i]), k * 10, m)
        for score, gain, grade, length, m in best.values():
            out.append([round(float(p[m, 0]), 5), round(float(p[m, 1]), 5), 'c', round(gain), round(grade * 100), length])
    print('climbs: found', len(out), flush=True)
    return out
