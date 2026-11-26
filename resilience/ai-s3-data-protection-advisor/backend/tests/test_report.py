import json
import re
from pathlib import Path

from openpyxl import load_workbook

from s3dp.derive import inventory
from s3dp.engine import scoring
from s3dp.report import excel, html

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / "sample-data" / "anycompany-inventory.json").read_text())
RAW = json.loads((Path(__file__).parent / "fixtures" / "raw_small.json").read_text())
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐❓❌✅]")


def test_excel_has_one_sheet_per_tab_and_no_formulas(tmp_path):
    data, _ = scoring.build(SAMPLE)
    data["tabs"][1]["cards"][0]["row"]["action"] = '=HYPERLINK("http://example.com")'
    out = excel.write_xlsx(data, str(tmp_path / "r.xlsx"))
    wb = load_workbook(out)
    names = wb.sheetnames
    assert names[0] == "Report Info" and "Governance" in names
    assert not any(n.startswith(("Opportunity", "Costs")) for n in names)
    assert not any(n.endswith("Questions") for n in names)
    assert sum(1 for ws in wb for row in ws.iter_rows() for c in row if c.data_type == "f") == 0
    assert not any(EMOJI.search(str(c.value)) for ws in wb for row in ws.iter_rows() for c in row if c.value)
    labels = [r[0] for r in wb["Report Info"].iter_rows(values_only=True)]
    assert "Analyst" not in labels and "Edition" not in labels


def test_excel_from_real_scan_with_unmeasured_sizes(tmp_path):
    data, _ = scoring.build(inventory.build(RAW))
    rows = [c["row"] for c in data["tabs"][1]["cards"]]
    assert any(r["size_bytes"] is None for r in rows)
    out = excel.write_xlsx(data, str(tmp_path / "scan.xlsx"))
    assert load_workbook(out).sheetnames[0] == "Report Info"
    assert html.render(data).startswith("<!doctype html>")


def test_html_escapes_script_breakout():
    data, _ = scoring.build(SAMPLE)
    data["meta"]["customerName"] = "</script><script>alert(1)</script>"
    page = html.render(data)
    assert "</script><script>alert(1)" not in page
    assert page.count("<script") == 2
    assert "innerHTML" not in page


def test_html_recommendations_show_the_description_the_narrative_rewrites():
    data, _ = scoring.build(SAMPLE)
    page = html.render(data)
    assert "['Description', r => r.description || '']" in page
    assert "r => r.why" not in page and "r => r.detail" not in page


def test_excel_recommendations_show_the_description_the_narrative_rewrites():
    data, _ = scoring.build(SAMPLE)
    tab = next(t for t in data["tabs"] if t["type"] == "recommendations")
    tab["items"][0]["description"] = "Rewritten by the narrative step."
    (sheet,) = excel._recommendations(tab)
    header, first = sheet["data"][0], sheet["data"][1]
    assert "Description" in header and "Why" not in header and "Detail" not in header
    assert first[header.index("Description")] == "Rewritten by the narrative step."


def _notice(path):
    return dict(load_workbook(path)["Report Info"].iter_rows(values_only=True))["Notice"]


def test_ai_notice_only_when_ai_ran(tmp_path):
    data, _ = scoring.build(SAMPLE)
    assert data["meta"]["ai"] == {"classification": False, "narrative": "off"}
    assert "Rules-based report: no generative AI was used." in html.render(data)
    assert _notice(excel.write_xlsx(data, str(tmp_path / "a.xlsx"))).startswith("Rules-based report")
    data["meta"]["ai"] = {"classification": True, "narrative": "complete"}
    assert "Text written by a generative AI assistant" in html.render(data)
    assert _notice(excel.write_xlsx(data, str(tmp_path / "b.xlsx"))).startswith("Text written by a generative AI")


def test_instructions_copy_rules_and_retrieval_text():
    inv = inventory.build(RAW)
    data, _ = scoring.build(inv)
    tab = data["tabs"][0]
    text = json.dumps(tab)
    assert not re.search(r"\b(click|please)\b", text, re.I) and "!" not in text
    assert "generative AI assistant" not in text and "Rules-based report: no generative AI was used." in text
    retrieval = next(s for s in tab["sections"] if s["title"] == "How the data was retrieved")["items"][0]
    assert "read-only credentials used for the scan" in retrieval and "deployed with this demo" not in retrieval
    for what in ("bucket settings", "AWS Backup settings", "daily S3 storage metrics", "S3 spend per region",
                 "IAM role names", "CloudFormation stack names", "AWS Config and Security Hub status"):
        assert what in retrieval, what
    inv["run"]["ai"] = {"classification": True, "narrative": "pending"}
    text = json.dumps(scoring.build(inv)[0]["tabs"][0])
    assert "classified by a generative AI assistant" in text and "Text written by a generative AI assistant" in text


def test_report_info_values_are_never_formulas(tmp_path):
    data, _ = scoring.build(SAMPLE)
    data["meta"]["customerName"] = '=HYPERLINK("http://example.com","x")'
    ws = load_workbook(excel.write_xlsx(data, str(tmp_path / "r.xlsx")))["Report Info"]
    cells = [c for row in ws.iter_rows() for c in row if c.value]
    assert not [c for c in cells if c.data_type == "f"]
    assert any(c.value == '=HYPERLINK("http://example.com","x")' for c in cells)


def test_excel_sizes_in_tib():
    data, _ = scoring.build(inventory.build(RAW))
    (sheet,) = excel._buckets(data["tabs"][1])
    header = sheet["data"][0]
    row = next(r for r in sheet["data"][1:] if r[header.index("Bucket")] == "orders-prod-eu-west-1")
    assert "Size (TiB)" in header and "Size (TB)" not in header
    assert row[header.index("Size (TiB)")] == 1.0
