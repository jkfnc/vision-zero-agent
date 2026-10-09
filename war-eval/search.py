import json, sys, urllib.request, urllib.parse
UA = {"User-Agent": "vision-zero-agent/0.1 (VAST Builders Challenge team-4 hackathon)"}
def search(q, n=50):
    p = {"action":"query","format":"json","generator":"search","gsrsearch":q+" filetype:video","gsrnamespace":"6","gsrlimit":str(n),
         "prop":"imageinfo","iiprop":"url|size|extmetadata|mediatype","iiextmetadatafilter":"LicenseShortName|Artist|Credit|ImageDescription|DateTimeOriginal"}
    r = urllib.request.Request("https://commons.wikimedia.org/w/api.php?"+urllib.parse.urlencode(p), headers=UA)
    return json.load(urllib.request.urlopen(r, timeout=30)).get("query",{}).get("pages",{})
out = {}
for q in sys.argv[1:]:
    for pid, pg in search(q).items():
        ii = pg.get("imageinfo",[{}])[0]; md = ii.get("extmetadata",{})
        lic = md.get("LicenseShortName",{}).get("value","")
        out[pg["title"]] = {"title":pg["title"],"url":ii.get("url"),"page":ii.get("descriptionurl"),"size_mb":round(ii.get("size",0)/1e6,1),
            "duration":ii.get("duration"),"license":lic,"artist":md.get("Artist",{}).get("value","")[:200],"credit":md.get("Credit",{}).get("value","")[:200],
            "desc":md.get("ImageDescription",{}).get("value","")[:300]}
json.dump(out, open("search.json","w"), indent=1)
for t,v in sorted(out.items(), key=lambda x: x[0]):
    if "public domain" in v["license"].lower() and v["size_mb"] < 160:
        print(f'{v["size_mb"]:6.1f}MB {str(round(v["duration"] or 0)):>5}s | {v["license"][:20]} | {t}')
