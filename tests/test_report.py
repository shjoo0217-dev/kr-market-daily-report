"""All companies, prices, IDs and receipts in this suite are invented."""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import deliver
import report
import sources

DAY = "20250108"
CODES = {"KOSPI": ["00000A", "00000B"], "KOSDAQ": ["00000C"], "KONEX": ["00000D"]}
CORP = "99990001"


def receipt(n=1, **fields):
    row = {"rcept_no": DAY + f"{n:06}", "rcept_dt": DAY, "corp_code": CORP,
           "stock_code": "00000A", "report_nm": "합성 보고", "corp_name": "가상회사", "corp_cls": "Y"}
    row.update(fields)
    return row


def page(rows, number=1):
    return {"status": "000", "page_no": number, "total_page": (len(rows) + 99) // 100,
            "page_count": 100, "total_count": len(rows), "list": rows[(number-1)*100:number*100]}


class Synthetic(sources.Live):
    synthetic = True

    def __init__(self):
        self.blocks = {}
        for market, codes in CODES.items():
            self.blocks[market, "master"] = [
                {"ISU_SRT_CD": code, "ISU_CD": "XX0000" + code, "ISU_NM": "가상" + code,
                 "LIST_DD": "20200102", "MKT_TP_NM": market} for code in codes]
            self.blocks[market, "trade"] = [
                {"ISU_CD": code, "ISU_NM": "가상" + code, "MKT_NM": market, "BAS_DD": DAY,
                 "TDD_CLSPRC": "120", "ACC_TRDVOL": "0", "ACC_TRDVAL": "0"} for code in codes]
        self.docs = [receipt(rm="정"), receipt(2, report_nm="[기재정정]합성 보고")]
        self.explicit = {("KOSPI", "00000B"): {"corp_code": CORP, "source": "합성 기간 자료"}}

    def krx(self, market, kind, day):
        return {"OutBlock_1": self.blocks[market, kind]}

    def companies(self):
        return {"00000A": [(CORP, DAY)], "00000C": [("99990002", DAY)]}

    def page(self, params):
        if params["last_reprt_at"] != "N" or params["bgn_de"] != params["end_de"]:
            raise AssertionError("invalid query")
        return page(self.docs, params["page_no"]) if params["corp_cls"] == "Y" else {"status": "013"}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.src = Synthetic()

    def build(self, day=DAY, closed=None):
        return report.build(self.src, day, self.src.explicit, closed)

    def test_all_markets_and_issuer_dedup(self):
        summary, rows, issuers = self.build()
        self.assertTrue(summary["complete"])
        for market in CODES:
            self.assertEqual(summary["markets"][market]["coverage_pct"], 100)
        self.assertEqual(summary["markets"]["KOSPI"]["corrections"], 1)
        self.assertEqual(len(issuers[CORP]), 2)
        self.assertEqual(rows["KOSPI"]["00000B"]["issuer_filing_count"], 2)
        self.assertEqual(rows["KOSDAQ"]["00000C"]["issuer_filing_count"], 0)
        self.assertIsNone(rows["KONEX"]["00000D"]["issuer_filing_count"])
        self.assertEqual(rows["KOSPI"]["00000A"]["trade"]["ACC_TRDVOL"], "0")

    def test_union_when_quote_missing_or_extra(self):
        self.src.blocks["KOSPI", "trade"][1]["ISU_CD"] = "00000Z"
        summary, rows, _ = self.build()
        self.assertFalse(summary["complete"])
        info = summary["markets"]["KOSPI"]
        self.assertEqual((info["coverage_pct"], info["trade_only"], info["missing_trade"]), (50, 1, 1))
        self.assertIsNone(rows["KOSPI"]["00000Z"]["master"])
        self.assertIsNone(rows["KOSPI"]["00000B"]["trade"])

    def test_invalid_dates_market_or_duplicates(self):
        for kind, mutation in [("trade", {"BAS_DD": "20250107"}), ("master", {"LIST_DD": "20301201"}),
                               ("master", {"ISU_SRT_CD": "00000B"}), ("trade", {"MKT_NM": "KONEX"})]:
            with self.subTest(mutation=mutation):
                self.src = Synthetic()
                self.src.blocks["KOSPI", kind][0].update(mutation)
                summary, _, _ = self.build()
                self.assertIsNone(summary["markets"]["KOSPI"][kind])
                self.assertIsNone(summary["markets"]["KOSPI"]["coverage_pct"])
                self.assertFalse(summary["complete"])

    def test_empty_weekday_closed_and_weekend(self):
        for key in self.src.blocks:
            self.src.blocks[key] = []
        self.src.docs = []
        for day, closed, expected in [(DAY, None, "미확인"), (DAY, "합성 휴장 공지", "휴장"),
                                      ("20250111", None, "휴장")]:
            summary, _, _ = self.build(day, closed)
            self.assertEqual(summary["markets"]["KOSPI"]["session"], expected)
            self.assertEqual(summary["complete"], expected == "휴장")
            self.assertEqual(summary["markets"]["KOSPI"]["filings"], 0)

    def test_failed_service_never_becomes_empty(self):
        self.src.page = lambda params: {"status": "020", "message": "hidden"}
        self.src.companies = lambda: sources.directory(b"<result><status>010</status></result>")
        summary, rows, _ = self.build()
        self.assertFalse(summary["complete"])
        self.assertIsNone(summary["markets"]["KOSPI"]["filings"])
        self.assertIsNone(rows["KOSPI"]["00000B"]["issuer_filing_count"])
        self.assertNotIn("hidden", str(summary))
        self.assertIn("010", summary["errors"]["OpenDART 고유번호"])

    def test_pagination_and_invalid_pages(self):
        docs = [receipt(n) for n in range(101)]
        self.assertEqual(len(sources.filings(lambda p: page(docs, p["page_no"]), DAY, "Y")), 101)
        for second in [{"status": "013"}, page(docs, 1), page(docs[:-1], 2),
                       dict(page(docs, 2), list=[docs[0]]), dict(page(docs, 2), list=[]),
                       dict(page(docs, 2), list=[receipt(105, rcept_dt="20250107")])]:
            with self.subTest(second=second.get("status")), self.assertRaises(sources.SourceError):
                sources.filings(lambda p: page(docs) if p["page_no"] == 1 else second, DAY, "Y")

    def test_link_ambiguity_and_historical_change(self):
        for candidates in [[(CORP, "20250109")], [(CORP, DAY), ("99990002", DAY)]]:
            self.assertIsNone(report.issuer_link("00000B", None, set(), candidates, DAY)[0])
        self.assertIsNone(report.issuer_link("00000B", {"corp_code": CORP}, {"99990002"}, [], DAY)[0])

    def test_directory_archive(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("CORPCODE.xml", "<result><list><stock_code>00000A</stock_code>"
                             "<corp_code>99990001</corp_code><modify_date>20250108</modify_date>"
                             "</list><list><stock_code> </stock_code></list></result>")
        self.assertEqual(sources.directory(buf.getvalue()), {"00000A": [(CORP, DAY)]})
        with self.assertRaises(sources.SourceError):
            sources.directory(b"unreadable")

    def test_endpoints_and_secret_routing(self):
        with patch.dict(os.environ, {"KRX_AUTH_KEY": "synthetic-krx", "OPENDART_API_KEY": "synthetic-dart"}):
            live = sources.Live()
        with patch("sources.get", return_value=b"{}") as get:
            for market, (prefix, _) in sources.MARKETS.items():
                for kind, suffix in [("trade", "bydd_trd"), ("master", "isu_base_info")]:
                    live.krx(market, kind, DAY)
                    get.assert_called_with(sources.KRX + prefix + "_" + suffix, {"basDd": DAY},
                                           {"AUTH_KEY": "synthetic-krx"})
            live.page({"bgn_de": DAY})
            get.assert_called_with(sources.DART + "list.json", {"bgn_de": DAY, "crtfc_key": "synthetic-dart"}, {})

    def test_cli_rerun_preserves_complete_and_no_stale_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = ["report.py", "--date", DAY, "--output", tmp]
            with patch.object(sys, "argv", args), patch("report.Live", return_value=self.src):
                self.assertEqual(report.main(), 0)
                complete = (root / DAY / "latest-complete").resolve()
                self.src.blocks["KOSPI", "master"].pop()
                self.src.blocks["KOSPI", "trade"].pop()
                self.src.page = lambda params: {"status": "010"}
                self.assertEqual(report.main(), 2)
            latest = root / DAY / "latest"
            self.assertNotEqual(complete, latest.resolve())
            self.assertEqual(complete, (root / DAY / "latest-complete").resolve())
            self.assertEqual(len(list((complete / "securities").glob("*/*.json"))), 4)
            self.assertEqual(len(list((latest / "securities").glob("*/*.json"))), 3)
            self.assertIn("공시 건수 미확인", (latest / "report.md").read_text())
            self.assertEqual(len(json.loads((complete / "issuers" / (CORP + ".json")).read_text())), 2)
            with patch("report.write_files", side_effect=OSError), self.assertRaises(OSError):
                report.publish(self.src, root, DAY, {}, None)
            self.assertEqual(len(list((root / DAY).glob("run-*"))), 2)

    def test_private_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            report.write_files(path, *self.build())
            with patch("deliver.api", return_value={"id": 123, "type": "group"}) as api:
                with self.assertRaises(sources.SourceError):
                    deliver.send(path, "123:synthetic", "123")
                self.assertEqual(api.call_count, 1)
            with patch("deliver.api", side_effect=[{"id": 123, "type": "private"},
                                                   {"chat": {"id": 123}, "document": {}}]) as api:
                deliver.send(path, "123:synthetic", "123")
                payload = api.call_args.args[2]
                self.assertIn(b"kr-report-20250108.zip", payload)
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    self.assertIn("report.md", archive.namelist())
                    self.assertIn("securities/KOSPI/00000A.json", archive.namelist())

    def test_validation(self):
        for raw in ["20250229", "2025018", "2025-01-08"]:
            with self.assertRaises(ValueError):
                sources.day_value(raw)
        with self.assertRaises(sources.SourceError):
            sources.krx_rows({"message": "error"}, "KOSPI", "master", DAY)
        self.assertFalse(report.correction(receipt(report_nm="[정정제출요구]합성")))


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        source = Synthetic()
        report.publish(source, Path("demo-output"), DAY, source.explicit, None)
        print("demo-output/20250108/latest/report.md")
    else:
        unittest.main()
