from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
import urllib.request, zipfile, tarfile, hashlib, json
from concurrent.futures import ThreadPoolExecutor
P=Path(__file__).resolve().parents[1]
CACHE=P/'artifacts/planning-work/booking_downloads'; CACHE.mkdir(parents=True,exist_ok=True)
SPECS=[
('civetweb','588860e30721bf5453b0440c390865a8e85dcae5','https://codeload.github.com/civetweb/civetweb/zip/588860e30721bf5453b0440c390865a8e85dcae5'),
('cjson','1.7.19','https://codeload.github.com/DaveGamble/cJSON/zip/refs/tags/v1.7.19'),
('sqlite','3.53.4','https://www.sqlite.org/2026/sqlite-amalgamation-3530400.zip'),
('unity','43f5ce1737022eb3f50787f3131ab4ddf5354349','https://codeload.github.com/ThrowTheSwitch/Unity/zip/43f5ce1737022eb3f50787f3131ab4ddf5354349'),
('sodium','1.0.22','https://github.com/jedisct1/libsodium/releases/download/1.0.22-RELEASE/libsodium-1.0.22-mingw.tar.gz')]
ALLOWED_HOSTS={'codeload.github.com','github.com','www.sqlite.org','objects.githubusercontent.com'}
def fetch(url):
 parts=urlsplit(url)
 if parts.scheme!='https' or parts.hostname not in ALLOWED_HOSTS:
  raise ValueError('Dependency URL outside allowlist: '+url)
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Lab-Booking-Fetch'}),timeout=90) as r:
  return r.read()
def one(s):
 name,version,url=s; archive=CACHE/(name+('.tar.gz' if name=='sodium' else '.zip'))
 if not archive.exists():
  archive.write_bytes(fetch(url))
 target=P/'vendor'/name; target.mkdir(exist_ok=True)
 if name=='sodium':
  with tarfile.open(archive) as a:
   for m in a.getmembers():
    parts=PurePosixPath(m.name).parts
    if not m.isfile() or '..' in parts: continue
    dest=target.joinpath(*parts); dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(a.extractfile(m).read())
 else:
  with zipfile.ZipFile(archive) as a:
   for m in a.infolist():
    parts=PurePosixPath(m.filename).parts[1:]
    if m.is_dir() or not parts or '..' in parts: continue
    rel='/'.join(parts)
    keep=(name=='civetweb' and (parts[0] in ('src','include') or rel.startswith(('LICENSE','CREDITS')))) or (name=='cjson' and rel in ('cJSON.c','cJSON.h','LICENSE')) or (name=='sqlite' and rel in ('sqlite3.c','sqlite3.h')) or (name=='unity' and (parts[0]=='src' or rel.startswith('LICENSE')))
    if keep:
     dest=target.joinpath(*parts); dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(a.read(m))
 return {'name':name,'version':version,'url':url,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
def patch_civetweb():
 path=P/'vendor/civetweb/src/civetweb.c'; text=path.read_text(encoding='utf-8')
 replacements=[
 ('if (h_chunk != NULL)\n\t    && mg_strcasecmp(cl, "identity")) {','if ((h_chunk != NULL) && mg_strcasecmp(h_chunk, "identity")) {'),
 ('(0!=mg_strcasecmp(cl, "chunked")) || (h_len!=NULL)','(0!=mg_strcasecmp(h_chunk, "chunked")) || (h_len!=NULL)'),
 ('conn->content_len = strtoll(cl, &endptr, 10);\n\t\tif ((endptr == cl) || (conn->content_len < 0)) {','errno = 0;\n\t\tconn->content_len = strtoll(h_len, &endptr, 10);\n\t\tif ((endptr == h_len) || (*endptr != 0) || (errno == ERANGE) || (conn->content_len < 0)) {')]
 for before,after in replacements:
  if before not in text: raise RuntimeError('Upstream patch context mismatch')
  text=text.replace(before,after,1)
 path.write_text(text,encoding='utf-8',newline='\n')
if __name__=='__main__':
 with ThreadPoolExecutor(max_workers=5) as pool: rows=list(pool.map(one,SPECS))
 patch_civetweb()
 (P/'docs/dependencies.lock.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
 print(json.dumps(rows,indent=2))
