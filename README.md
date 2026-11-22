# 한국 주식 일일 보고서

KOSPI·KOSDAQ·KONEX의 기준일별 주권 목록과 매매행을 대조하고 그날 접수한 발행사 공시를 모읍니다. 한국어 보고서에서 종목별 JSON과 공시 원문을 열 수 있습니다. Python 3.9 이상과 표준 라이브러리만 사용합니다. 실행 환경은 macOS 또는 Linux입니다.

## 키 없이 확인하기

```sh
python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 tests/test_report.py --demo
```

두 번째 명령은 `demo-output/20250108/latest/report.md`를 만듭니다. 합성 주권 4개 중 두 주권이 같은 발행사에 연결되고, 시장 공시는 원본과 정정 각 1건으로 집계됩니다. 테스트는 외부 네트워크와 계정이 필요 없습니다.

## KRX 이용 신청

KRX Data Marketplace에 로그인해 **마이페이지 → API 인증키 신청**에서 키 승인 상태를 확인합니다. **서비스 이용 → 주식**에서 아래 여섯 서비스를 각각 열고 **API 이용신청**을 누릅니다. 기간·목적을 입력한 후 **마이페이지 → 이용현황**에서 서비스별 이용 승인과 유효기간을 확인합니다. 인증키 발급과 개별 서비스 이용 승인은 각각 필요합니다. [공식 이용 절차](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) · [서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)

호스트는 `https://data-dbg.krx.co.kr`입니다. 아래 운영 경로에 HTTP GET으로 `?basDd=YYYYMMDD`를 붙이고, 헤더 `AUTH_KEY`에 승인된 키를 전달합니다. 공식 페이지의 **개발 명세서 다운로드**에서 운영 URL과 필드를 확인할 수 있습니다. 샘플 테스트는 별도 경로인 `/svc/sample/`을 사용합니다.

| 시장 | 이용 신청명 | 운영 경로 | 공식 상세 |
|---|---|---|---|
| KOSPI | 유가증권 종목기본정보 | `/svc/apis/sto/stk_isu_base_info` | [명세·신청](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=PiwgMdTwmsenXhmqqxuj) |
| KOSPI | 유가증권 일별매매정보 | `/svc/apis/sto/stk_bydd_trd` | [명세·신청](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC) |
| KOSDAQ | 코스닥 종목기본정보 | `/svc/apis/sto/ksq_isu_base_info` | [명세·신청](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=CifLHplnUFMgpHIMMPXs) |
| KOSDAQ | 코스닥 일별매매정보 | `/svc/apis/sto/ksq_bydd_trd` | [명세·신청](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=hZjGpkllgCBCWqeTsYFj) |
| KONEX | 코넥스 종목기본정보 | `/svc/apis/sto/knx_isu_base_info` | [명세·신청](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=COgTLqgmGlqyJvaEFNIc) |
| KONEX | 코넥스 일별매매정보 | `/svc/apis/sto/knx_bydd_trd` | [명세·신청](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=HSiRvxGSYnvaKuAuqpqp) |

서비스 목록상 제공 시작일은 KOSPI·KOSDAQ 2010-01-04, KONEX 2013-07-01입니다. 전 시장을 대조하려면 KONEX 개장 이후의 날짜를 선택합니다.

## OpenDART 연결

OpenDART의 **인증키 신청/관리**에서 발급한 키를 사용합니다. 프로그램이 `crtfc_key`를 요청 시 추가합니다. 고유번호 목록은 실행당 한 번, 공시검색은 시장별로 한 거래일의 전체 페이지를 요청합니다.

| 서비스 | GET 엔드포인트 | 용도 |
|---|---|---|
| 고유번호 | `https://opendart.fss.or.kr/api/corpCode.xml` | ZIP 안의 CORPCODE.xml에서 발행사 고유번호와 대표 종목코드, 최종변경일 확인. [공식 명세](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018) |
| 공시검색 | `https://opendart.fss.or.kr/api/list.json` | `bgn_de=end_de=기준일`, `corp_cls=Y/K/N` 각각 요청. `last_reprt_at=N`, 페이지당 100건. [공식 명세](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001) |

## 로컬 실수집

승인된 비밀 관리 도구가 환경변수 `KRX_AUTH_KEY`, `OPENDART_API_KEY`를 주입하면 바로 실행할 수 있습니다. 대화형 zsh에서 한 세션 동안만 키를 입력하려면 아래 명령을 사용합니다. 프롬프트 입력은 화면과 셸 명령 이력에 남지 않습니다.

```zsh
read -rs 'KRX_AUTH_KEY?KRX 인증키 입력 후 Enter '; echo
read -rs 'OPENDART_API_KEY?OpenDART 인증키 입력 후 Enter '; echo
export KRX_AUTH_KEY OPENDART_API_KEY
python3 report.py --date 20261120
unset KRX_AUTH_KEY OPENDART_API_KEY
```

날짜를 생략하면 한국 시간 전날을 조회합니다. 지정한 날짜 그대로 재실행하며 이전 거래일로 대체하지 않습니다. 실행 종료 코드 0은 원천 수집·주권 대조 완료, 2는 점검 필요 또는 입력 오류입니다. 입력이 유효하고 수집이 부분 실패하면 확보한 자료와 진단을 함께 기록합니다.

결과는 기본적으로 `private-output/YYYYMMDD/latest/`에 생성됩니다. `--output /개인/저장경로`로 목적지를 바꿀 수 있습니다. 날짜 단위로 동시 실행을 잠그고 새 디렉터리에 파일을 모두 쓴 뒤 링크를 교체합니다. 각 실행본은 보존되며 `latest-complete`는 마지막 수집·대조 완료본을 가리킵니다.

| 파일 | 확인할 내용 |
|---|---|
| `report.md` | 시장별 상태와 커버리지, 오류, 공시 제목·원문 링크, 전 종목 표 |
| `summary.json` | 원천 성공·오류, 분모·분자, 매매 누락·초과, 연결되지 않은 발행사 수와 주권 수 |
| `securities/시장/6자리코드.json` | 기본·매매 원천 필드, 발행사 연결 근거, 해당 발행사의 접수번호 |
| `issuers/8자리발행사코드.json` | 해당 날짜에 관측한 발행사별 공시 원문 메타데이터 |

## 판정 기준

- 종목기본정보의 단축코드 `ISU_SRT_CD`와 매매정보의 `ISU_CD`를 시장 안에서 연결합니다. 기본정보의 `ISU_CD`는 12자리 표준코드입니다. 한쪽에만 있는 주권도 파일로 남기며 거래량 0도 유효한 매매행으로 취급합니다.
- 매매행은 모든 `BAS_DD`와 시장을 검증합니다. 기본정보의 공식 출력에는 기준일이 없으므로 요청한 `basDd`, 응답 시장, 상장일이 기준일 이하인지 대조합니다. 잘못된 날짜·코드 중복·응답 구조 오류가 있으면 해당 서비스 전체를 실패로 기록합니다.
- 커버리지는 **매매행과 일치하는 기본 종목 수 ÷ 기본 종목 수**입니다. 두 원천을 대조할 수 있을 때만 계산합니다. 수집 실패값은 JSON에서 `null`입니다.
- 검증된 당일 매매행이 있으면 거래일입니다. 매매행이 없는 토·일요일 또는 근거를 등록한 평일은 휴장입니다. 평일에 매매행과 휴장 근거가 모두 없으면 미확인으로 남습니다. 휴장일에도 원천 오류와 공시검색 상태를 별도로 기록합니다.
- 공시는 접수일·법인구분·페이지 합계·접수번호 중복을 검증합니다. 첫 페이지의 `013` 또는 정상 응답의 총 0건만 공시 0건으로 표시합니다. 뒤 페이지 실패 시 해당 시장 전체 건수는 미확인입니다. 정정 건수는 제목의 `[기재정정]`, `[첨부정정]`, `[정정]` 표식을 셉니다. 원본에 표시되는 후속 정정 비고 `정`과 정정제출요구는 별도로 원문에 보존합니다.
- 발행사 연결에는 당일 공시의 직접 코드, 기간과 출처가 명시된 주권 연결, 고유번호 목록의 단일 대표 코드를 사용합니다. 현행 고유번호 자료의 변경일이 기준일보다 뒤인 경우 과거 연결 근거로 쓰지 않습니다. 최신 목록으로 과거 발행사 전체를 복원할 수 없으며, 우선주 등은 별도의 근거가 필요할 수 있습니다. 이름이나 코드 접두어가 같은 것만으로 연결하지 않습니다.
- 시장 공시는 접수번호로 한 번씩 집계합니다. 여러 주권이 한 발행사를 공유할 수 있어 종목별 공시 수를 더하면 중복됩니다. 발행사 미확인 또는 세 시장 중 공시검색 실패가 있으면 종목별 공시 건수는 미확인입니다. 주권과 연결할 수 없는 공시도 발행사 파일에 보존합니다. 수집·대조 완료본에도 발행사 미연결 주권은 있을 수 있습니다.

### 휴장 공지 및 주권 연결 보완

`--config /개인/경로/config.json`으로 휴장 근거와 추가 발행사 연결을 지정할 수 있습니다. 아래는 형식 설명용 합성 자료입니다. 실제 설정에는 확인한 KRX 공지와 효력 기간을 적습니다. 기본값은 빈 설정입니다.

```json
{
  "closed_days": {"20250109": "합성 휴장 공지 · 실제 사용 시 공식 공지 URL"},
  "issuer_links": [
    {"market": "KOSPI", "code": "00000B", "corp_code": "99990001",
     "from": "20250101", "through": "20251231", "source": "합성 주권 명부 · 실제 사용 시 확인한 출처"}
  ]
}
```

## 공개 저장소에서 GitHub Actions 실행

공개 저장소에는 소스·합성 테스트를 두고, 실제 결과는 자신의 Telegram 봇과 맺은 개인 대화에 ZIP 파일로 받습니다. 실제 데이터는 Git 커밋, Actions 아티팩트, 캐시, 작업 요약에 올리지 않습니다. 공개 Actions 화면에는 실행 시각과 작업 성공·실패가 표시됩니다. 수집 로그와 운영 파일은 임시 러너 안에만 기록합니다. 로컬만 사용하면 Telegram 설정은 생략합니다.

1. Telegram에서 공식 **@BotFather**로 개인 봇을 만들고 그 봇과 1:1 대화를 시작합니다. 본인의 개인 대화 숫자 ID를 준비합니다. 코드는 전송 전 `getChat`으로 지정한 ID와 대화 유형 `private`를 확인합니다. [Telegram 봇 안내](https://core.telegram.org/bots/features#botfather) · [Bot API](https://core.telegram.org/bots/api#senddocument)
2. GitHub 저장소 **Settings → Secrets and variables → Actions → Secrets → New repository secret**에서 아래 네 값을 각각 등록합니다. 채팅·README·워크플로 입력·일반 Variables에는 키 값을 입력하지 않습니다. [GitHub Secrets 안내](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
3. 같은 화면 **Variables → New repository variable**에 `REPORT_DELIVERY`를 만들고 값으로 `telegram`을 넣습니다. 이 값이 있어야 기본 브랜치의 수동·정기 실행이 실제 수집과 개인 대화 전송을 수행합니다.
4. **Actions → 한국 주식 일일 보고서 → Run workflow**에서 기본 브랜치를 선택하고 `date`에 과거 거래일을 입력합니다. 전송받은 ZIP을 풀어 `report.md`의 시장별 수치와 점검 내역을 확인합니다. 일부 원천이 실패해도 생성된 보고서는 전송하고 작업을 실패로 표시합니다.
5. 기본 브랜치에서 매일 **10:17 KST**(01:17 UTC)에 전날 자료를 요청합니다. 실행은 GitHub 대기열에 따라 지연될 수 있습니다. 같은 날짜로 다시 돌리면 새 ZIP이 도착합니다. 새 코드가 main에 게시되면 키 없이 합성 검증만 수행합니다. [워크플로 트리거](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)

| 설정 | 종류 | 값 |
|---|---|---|
| `KRX_AUTH_KEY` | Secret | 여섯 서비스 이용 승인을 받은 KRX 키 |
| `OPENDART_API_KEY` | Secret | 금융감독원 OpenDART 인증키 |
| `TELEGRAM_BOT_TOKEN` | Secret | 개인 봇 토큰 |
| `TELEGRAM_CHAT_ID` | Secret | 수신자 본인의 개인 대화 숫자 ID |
| `REPORT_CONFIG_JSON` | 선택 Secret | 휴장 근거·발행사 연결 JSON |
| `REPORT_DELIVERY` | Variable | `telegram`이면 운영 실행 활성화 |

정상 거래일에 KRX는 6회, DART는 고유번호 1회와 시장별 페이지 수만큼 호출합니다. 외부 DB나 상시 서버가 필요 없습니다. 원천의 인증, 이용 승인, IP 제한, 일시 장애는 보고서 점검 내역에서 확인합니다. KRX 데이터는 승인받은 용도로 개인 보관하고 공개 재배포를 피합니다. [KRX 이용약관](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp)
