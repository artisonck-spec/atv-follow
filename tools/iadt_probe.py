"""What is inside the official protected-area files (data.gov.lv): layers, fields, sample rows, coordinate system."""
import io, zipfile, urllib.request, collections
import shapefile  # pyshp
URLS = {
    'iadt': 'https://data.gov.lv/dati/dataset/b2de2313-c20e-4ebf-a9c0-b8099566320a/resource/aa165932-8835-41a0-baa2-3336d2e8e6ef/download/ipasi_aizsargajamas_dabas_teritorijas.zip',
    'mikro': 'https://data.gov.lv/dati/dataset/fa50b0fb-04f2-452f-ade9-f8448d94eefa/resource/4770cc11-02eb-42e1-98c9-e640e3d8b7ff/download/mikroliegumi.zip',
}
for k, u in URLS.items():
    b = urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'ATV-Trails'}), timeout=120).read()
    z = zipfile.ZipFile(io.BytesIO(b))
    print('=====', k, len(b), 'bytes'); print([n for n in z.namelist()])
    for n in z.namelist():
        if n.lower().endswith('.prj'): print('PRJ', n, z.read(n)[:200])
        if not n.lower().endswith('.shp'): continue
        base = n[:-4]
        r = shapefile.Reader(shp=io.BytesIO(z.read(n)), dbf=io.BytesIO(z.read(base + '.dbf')), encoding='utf-8', encodingErrors='replace')
        print('--- layer', n, r.shapeTypeName, len(r), 'records'); print('fields', [f[0] for f in r.fields[1:]])
        for i in range(min(3, len(r))): print('  ', r.record(i).as_dict())
        for f in [f[0] for f in r.fields[1:]]:
            vals = collections.Counter(str(r.record(i)[f]) for i in range(len(r)))
            if len(vals) < 40: print('  values', f, dict(vals.most_common(40)))
        print('  bbox', r.bbox)
