"""Test exact receipt-based OpenDART document availability before a bounded market-wide request."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import csv,gzip,hashlib,io,json,os,re,time,urllib.parse,urllib.request,xml.etree.ElementTree as ET,zipfile

HERE=Path(__file__).resolve().parent
ROOT=Path(os.environ.get('OUT_DIR','artifacts'))/('prelim_'+os.environ['PART'])
KEY=os.environ.get('OPENDART_API_KEY','').strip().encode()
KST=timezone(timedelta(hours=9))
ROOT.mkdir(parents=True,exist_ok=True)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None
http=urllib.request.build_opener(NoRedirect())
def mark(name,obj):
    line=json.dumps(obj,ensure_ascii=False)
    if KEY and KEY in line.encode():raise RuntimeError('guard')
    with (ROOT/name).open('a',encoding='utf8') as f:f.write(line+'\n')
def now():return datetime.now(KST).isoformat(timespec='seconds')
class Run:
    def __init__(self):self.stop='';self.size=0;self.count=0;self.good=set();self.tried=set();self.start=time.monotonic()
    def one(self,no):
        if self.stop or no in self.tried:return
        self.count+=1;self.tried.add(no)
        req={'endpoint':'document.xml','rcept_no':no,'started_kst':now()}
        par=urllib.parse.urlencode({'crtfc_key':KEY.decode(),'rcept_no':no})
        try:
            with http.open('https://opendart.fss.or.kr/api/document.xml?'+par,timeout=12) as res:body=res.read(12*1024**2+1)
            if len(body)>12*1024**2 or KEY in body:raise ValueError('guard_or_size')
            if not zipfile.is_zipfile(io.BytesIO(body)):
                s=ET.fromstring(body).findtext('status') or 'unrecognized'
                if not re.fullmatch('[0-9]{3}',s):s='unrecognized'
                req['status']=s
                if s in ('010','011','012','020','901'):self.stop='provider_'+s
                return
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                if sum(v.file_size for v in z.infolist())>40*1024**2:raise ValueError('oversize_zip')
                named=False;title=False
                for name in z.namelist():
                    content=z.read(name)
                    if KEY in content:raise ValueError('guard')
                    if Path(name).name==no+'.xml':named=True
                    if '잠정'.encode() in content or '잠정'.encode('cp949') in content:title=True
                if not named or not title:
                    req['status']='quarantine_unverified_filename_or_title';return
            dst=ROOT/'raw';dst.mkdir(exist_ok=True)
            target=dst/(no+'.zip');target.write_bytes(body);self.size+=len(body);self.good.add(no)
            req.update(status='ok',sha256=hashlib.sha256(body).hexdigest(),path='raw/'+target.name,bytes=len(body))
        except Exception as exc:
            req['status']=type(exc).__name__
            if hasattr(exc,'code'):
                req['http_status']=exc.code
                if exc.code in (401,403,429):self.stop='provider_http_'+str(exc.code)
        finally:
            req['ended_kst']=now();mark('requests.jsonl',req);time.sleep(.12)
    def run(self):
        if not KEY:self.stop='missing_key';return
        pilots=(HERE/'verified_pilot.txt').read_text().split()
        ids=(HERE/('next_'+os.environ['PART']+'.txt')).read_text().split()
        if not all(re.fullmatch(r'\d{14}',x) for x in pilots+ids):self.stop='invalid_input';return
        for no in pilots:self.one(no)
        if not self.good:self.stop=self.stop or 'verified_pilots_returned_no_usable_receipt_bodies';return
        for no in ids:
            if self.stop:break
            if self.size>=350*1024**2 or time.monotonic()-self.start>=2950:self.stop='resource_budget';break
            self.one(no)
if __name__=='__main__':
    job=Run()
    try:job.run()
    except Exception as exc:job.stop='internal_'+type(exc).__name__
    mark('status.jsonl',dict(status=job.stop or 'completed',checked=job.count,usable=len(job.good),bytes=job.size,ended_kst=now()))
    print("Receipt collection completed. See the encrypted artifact.")
