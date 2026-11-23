"""Authenticated OpenDART consolidated and separate income amounts for candidate screening.
Latest-vintage amounts are linked to their actual rcept_no; never backdate them.
"""
import collections, csv, datetime as dt, gzip, hashlib, io, json, os, pathlib, re, time
import urllib.error, urllib.parse, urllib.request, xml.etree.ElementTree as ET, zipfile
OUT=pathlib.Path(os.environ.get("OUT_DIR","out"))/"facts"
KEY=os.environ.get("OPENDART_API_KEY","").strip()
KST=dt.timezone(dt.timedelta(hours=9))
class ClosedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None
OPEN=urllib.request.build_opener(ClosedRedirect())
def now():return dt.datetime.now(KST).isoformat(timespec="seconds")
def record(name,row):
    text=json.dumps(row,ensure_ascii=False)
    if KEY and KEY in text:raise RuntimeError("guard")
    with (OUT/name).open("a",encoding="utf8")as f:f.write(text+"\n")
class Stop(Exception):pass
def fetch(endpoint,params):
    assert endpoint in ("fnlttMultiAcnt.json","corpCode.xml","list.json")
    safe={"at":now(),"service":endpoint,"params":params}
    q=urllib.parse.urlencode({"crtfc_key":KEY,**params})
    for attempt in range(1,4):
        try:
            with OPEN.open("https://opendart.fss.or.kr/api/"+endpoint+"?"+q,timeout=16)as stream:raw=stream.read(18_000_001)
            if len(raw)>18_000_000 or KEY.encode() in raw:raise ValueError()
            if endpoint=="corpCode.xml" and zipfile.is_zipfile(io.BytesIO(raw)):
                with zipfile.ZipFile(io.BytesIO(raw))as z:
                    members=[a for a in z.infolist() if a.filename.upper().endswith("CORPCODE.XML")]
                    if len(members)!=1 or members[0].file_size>160_000_000:raise ValueError()
                    xml=z.read(members[0])
                if KEY.encode() in xml:raise ValueError()
                obj=ET.fromstring(xml);status="000"
            elif endpoint=="corpCode.xml":
                obj=ET.fromstring(raw);status=obj.findtext("status","unknown")
            else:
                obj=json.loads(raw);status=str(obj.get("status","unknown"))
            safe.update(status=status,attempt=attempt,end=now(),sha256=hashlib.sha256(raw).hexdigest())
            record("requests.jsonl",safe)
            if status in ("010","011","012","020","901"):raise Stop("provider_"+status)
            time.sleep(.10)
            return obj if status=="000" else None
        except urllib.error.HTTPError as err:
            state="http_"+str(err.code)
            if err.code not in (429,500,502,503,504):break
        except (OSError,ValueError,TimeoutError,ET.ParseError):state="io_or_parse"
        if attempt<3:time.sleep(2**attempt)
    record("requests.jsonl",{**safe,"status":state,"end":now()});return None
def gather():
    if not KEY:raise Stop("credential_unavailable")
    pilot=fetch("fnlttMultiAcnt.json",{"corp_code":"00126380,00164779","bsns_year":"2025","reprt_code":"11011"})
    if not pilot or not any(x.get("sj_div") in ("IS","CIS") and x.get("rcept_no") for x in pilot.get("list",[])):
        raise Stop("pilot_no_verified_income")
    mappings=collections.defaultdict(set)
    corp=fetch("corpCode.xml",{})
    if corp is None:raise Stop("corpCode_unavailable")
    for row in corp.findall(".//list"):
        c=(row.findtext("corp_code")or"").strip();s=(row.findtext("stock_code")or"").strip()
        if re.fullmatch(r"\d{8}",c) and re.fullmatch(r"[0-9A-Z]{6}",s):
            mappings[c].add((s,"current_corpCode"))
    optional=pathlib.Path(__file__).with_name("facts_history.csv")
    if optional.is_file():
        for r in csv.DictReader(optional.open(encoding="utf-8-sig")):
            if re.fullmatch(r"\d{8}",r["corp_code"]) and re.fullmatch(r"[0-9A-Z]{6}",r["stock_code"]):
                mappings[r["corp_code"]].add((r["stock_code"],"existing_receipt"))
    # Add issuers reporting as KOSPI or KOSDAQ at the time, including later delistings.
    begin=dt.date(2025,1,1);last=dt.date(2026,11,23)
    while begin<=last:
        end=min(last,begin+dt.timedelta(days=88))
        for m in ("Y","K"):
            page=1
            while True:
                vals=dict(bgn_de=begin.strftime("%Y%m%d"),end_de=end.strftime("%Y%m%d"),page_no=str(page),page_count="100",corp_cls=m,pblntf_ty="A",last_reprt_at="N")
                response=fetch("list.json",vals)
                if not response:break
                for x in response.get("list",[]):
                    c=x.get("corp_code","");s=x.get("stock_code","")
                    if re.fullmatch(r"\d{8}",c) and re.fullmatch(r"[0-9A-Z]{6}",s):
                        mappings[c].add((s,"receipt_"+x.get("rcept_no","")))
                if page>=int(response["total_page"]):break
                page+=1
        begin=end+dt.timedelta(days=1)
    ids=sorted(mappings)
    with gzip.open(OUT/"issuer_mapping.csv.gz","wt",encoding="utf8",newline="")as f:
        w=csv.writer(f);w.writerow(["corp_code","stock_code","source"]);w.writerows((c,s,k) for c in ids for s,k in sorted(mappings[c]))
    period=[(y,r)for y in (2024,2025,2026) for r in ("11013","11012","11014","11011") if (y,r)!=(2026,"11011")]
    cols="rcept_no reprt_code bsns_year corp_code stock_code fs_div fs_nm sj_div sj_nm account_nm thstrm_nm thstrm_dt thstrm_amount thstrm_add_amount frmtrm_nm frmtrm_dt frmtrm_amount frmtrm_add_amount bfefrmtrm_nm bfefrmtrm_dt bfefrmtrm_amount currency ord".split()
    n=0
    with gzip.open(OUT/"income_accounts.csv.gz","wt",encoding="utf8",newline="")as f:
        writer=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore");writer.writeheader()
        for y,r in period:
            for i in range(0,len(ids),100):
                batch=ids[i:i+100]
                payload=fetch("fnlttMultiAcnt.json",{"corp_code":",".join(batch),"bsns_year":str(y),"reprt_code":r})
                if not payload:continue
                rows=[x for x in payload.get("list",[]) if x.get("sj_div") in ("IS","CIS") and x.get("corp_code") in batch and x.get("bsns_year")==str(y) and x.get("reprt_code")==r and re.fullmatch(r"\d{14}",x.get("rcept_no",""))]
                writer.writerows(rows);n+=len(rows)
    return {"status":"done","issuers":len(ids),"income_rows":n,"periods":len(period),"vintage":"contemporary API retrieval, actual receipt references, no historical as-of or cash flow certification"}
if __name__=="__main__":
    OUT.mkdir(parents=True,exist_ok=True)
    try:summary=gather()
    except Stop as e:summary={"status":str(e)}
    (OUT/"status.json").write_text(json.dumps(summary,ensure_ascii=False))
    print(json.dumps(summary,ensure_ascii=False))
