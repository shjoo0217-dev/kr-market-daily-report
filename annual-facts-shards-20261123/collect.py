"""Bounded read-only official filings collection. Preserve each response as it arrives."""
from pathlib import Path
import csv,os,gzip,hashlib,json,time,io,zipfile,re,urllib.request,urllib.parse,xml.etree.ElementTree as ET
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
HERE=Path(__file__).resolve().parent;part=os.environ.get('PART','priority');stage=int(os.environ.get('STAGE','0'));assert stage in (0,1,2);root=Path(os.environ.get('OUT_DIR','out'))/('facts_'+part+'_'+str(stage));root.mkdir(exist_ok=True,parents=True);(root/'raw').mkdir(exist_ok=True)
secret=os.environ.get('OPENDART_API_KEY','').strip();deadline=time.monotonic()+170;KST=timezone(timedelta(hours=9));lock=Lock();spec=__import__('issuer_map').public_issuer_map();summary={'part':part,'requests':0,'success':0,'rows':0,'status':'started'}
def stamp():return datetime.now(KST).isoformat(timespec='seconds')
class LocalOnly(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kw):return None

def fetch(endpoint,args,label):
 if not secret:return None
 assert endpoint in ('corpCode.xml','fnlttMultiAcnt.json');now=stamp();query=urllib.parse.urlencode({'crtfc_key':secret,**args});url='https://opendart.fss.or.kr/api/'+endpoint+'?'+query;ret=None;count=0;sha='';fname='';state='unknown'
 try:
  with urllib.request.build_opener(LocalOnly()).open(url,timeout=12)as h:raw=h.read(17_000_001)
  if len(raw)>17_000_000 or secret.encode() in raw:raise ValueError('invalid_content')
  if endpoint=='corpCode.xml' and zipfile.is_zipfile(io.BytesIO(raw)):
   with zipfile.ZipFile(io.BytesIO(raw)) as z:
    m=z.getinfo('CORPCODE.xml');assert m.file_size<150_000_000;contents=z.read(m)
   if secret.encode() in contents:raise ValueError('invalid_content')
   ret=ET.fromstring(contents);state='000';count=len(ret)
  else:
   doc=json.loads(raw) if raw.strip().startswith(b'{') else {};state=str(doc.get('status','not_json'));ret=doc if state=='000' else None;count=len(doc.get('list',[]))
  if ret is not None:
   sha=hashlib.sha256(raw).hexdigest();fname='raw/'+label+('.zip' if endpoint=='corpCode.xml' else '.json.gz');path=root/fname;path.write_bytes(raw if endpoint=='corpCode.xml' else gzip.compress(raw,compresslevel=2,mtime=0))
 except Exception as err:state=type(err).__name__
 row=dict(label=label,service=endpoint,params=args,requested=now,received=stamp(),status=state,rows=count,sha256=sha,raw_file=fname)
 with lock:
  with (root/'requests.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
  summary['requests']+=1;summary['success']+=int(state=='000');summary['rows']+=count if endpoint!='corpCode.xml' else 0
 return ret

def run():
 if not secret:return 'credential_absent'
 by={r['stock']:{r['corp']} if r['corp'] else set() for r in spec};pilots={'005930':'00126380','000660':'00164779'}
 if part=='0' and stage==0:fetch('fnlttMultiAcnt.json',dict(corp_code=','.join(pilots.values()),bsns_year='2026',reprt_code='11014'),'pilot_2026_11014')
 corpus=fetch('corpCode.xml',{},'corp');
 if corpus is not None:
  for node in corpus.findall('./list'):
   c=(node.findtext('stock_code')or'').strip();issuer=(node.findtext('corp_code')or'').strip()
   if c in by and re.fullmatch(r'\d{8}',issuer):by[c].add(issuer)
 all_stocks=sorted(by)
 selected=all_stocks[int(part)::4]
 ambiguous=[c for c in selected if len(by[c])!=1];ids=sorted({next(iter(by[c]))for c in selected if len(by[c])==1});
 with (root/'selected.csv').open('w')as f:
  w=csv.writer(f);w.writerow(['stock','corp','mapping_count']);w.writerows((c,'|'.join(sorted(by[c])),len(by[c])) for c in selected)
 periods=[(2025,'11014'),(2026,'11014'),(2025,'11012'),(2026,'11012'),(2024,'11014'),(2024,'11012'),(2025,'11011'),(2024,'11011'),(2025,'11013'),(2026,'11013'),(2024,'11013')];periods=periods[stage*4:(stage+1)*4];sz=80; tasks=[(yr,rep,j,ids[j:j+sz]) for yr,rep in periods for j in range(0,len(ids),sz)];summary.update(eligible=len(selected),identities=len(ids),missing_or_ambiguous=len(ambiguous),planned=len(tasks));
 def one(t):
  if time.monotonic()>deadline:return
  yr,rep,j,batch=t;fetch('fnlttMultiAcnt.json',dict(corp_code=','.join(batch),bsns_year=str(yr),reprt_code=rep),f'{yr}_{rep}_{j:04d}')
 with ThreadPoolExecutor(max_workers=2)as ex:list(ex.map(one,tasks))
 return 'completed_or_internal_time_budget'
try:summary['status']=run()
except Exception as err:summary['status']=type(err).__name__
(root/'status.json').write_text(json.dumps(summary));print(json.dumps(summary))
