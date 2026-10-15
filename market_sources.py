"""P1 approved-source market adapters. No source is treated as causal evidence.

All collectors return validated observations; persistence is a separate transaction.
No automatic network calls from map requests. Python 3.10+ standard library.
"""
import json
import math
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

MAX_RESPONSE = 9_000_000
USER_AGENT = "ConflictScope/3.0 (+https://github.com/xia-ke/ConflictScope)"
PINK_SHEET_PAGE = "https://www.worldbank.org/en/research/commodity-markets"
PINK_SHEET_DOC = "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx"
COMTRADE_PAGE = "https://comtradeapi.un.org/"
EIA_PAGE = "https://www.eia.gov/opendata/"
EIA_SERIES = {"RBRTE": ("oil", "Europe Brent Spot", "USD", "barrel"),
              "RWTC": ("oil", "Cushing WTI Spot", "USD", "barrel")}
COMMODITIES = {
    "oil": ("Crude oil", "energy", "270900"),
    "wheat": ("Wheat (other than durum)", "agriculture", "100199"),
    "copper": ("Refined copper cathodes", "metals", "740311"),
    "gold": ("Gold unwrought, other non-monetary", "metals", "710812"),
}
# UN M49 numerical reporters for first pilots. Not a complete global mapping.
REPORTERS = {"UKR": 804, "RUS": 643, "COD": 180, "YEM": 887, "SDN": 729}
HS_REVISION = "HS2017"
COMTRADE_CLASSIFICATION = "H5"
# Explicitly reviewed cross-revision HS6 mapping. Do not silently reclassify
# an annual series or substitute another HS revision after an empty response.
# HS2022 100199 is confirmed in the WCO 2022 Chapter 10 nomenclature.
HS_REVISION_CODES = {
    "HS2017": ("H5", {key: item[2] for key,item in COMMODITIES.items()}),
    "HS2022": ("H6", {"wheat":"100199"}),
}


def retrieved_at():
    return datetime.now(timezone.utc).isoformat()


def finite_price(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Invalid numeric observation")
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Price must be finite and positive")
    return number


def fetch_bytes(url, *, max_size=MAX_RESPONSE, headers=None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in (
        "comtradeapi.un.org", "api.eia.gov", "thedocs.worldbank.org"
    ):
        raise ValueError("Unapproved data origin")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=20) as response:
        redirect = urllib.parse.urlsplit(response.geturl())
        if redirect.scheme != "https" or redirect.hostname not in (
            "comtradeapi.un.org", "api.eia.gov", "thedocs.worldbank.org"
        ):
            raise ValueError("Redirect to unapproved data origin")
        raw = response.read(max_size + 1)
    if len(raw) > max_size:
        raise ValueError("Source exceeds size limit")
    return raw


def fetch_json(url, **kwargs):
    doc = json.loads(fetch_bytes(url, **kwargs).decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("Expected JSON object")
    return doc


def comtrade(reporter, year, *, commodity, flow="export", hs_revision="HS2017",
             key=None, loader=fetch_json):
    """Fetch ONE exact explicitly selected HS6 world aggregate from final Data API.

    Preview is intentionally never persisted: it may be partial or empty and cannot
    establish a zero trade total. Free UN Comtrade B2C API keys support data/v1/get;
    the supplied secret must stay in an HTTPS header, not in URLs or logs.
    """
    if reporter not in REPORTERS or commodity not in COMMODITIES:
        raise ValueError("Unsupported pilot reporter or commodity")
    if type(year) is not int or flow not in ("export","import") or not 1990 <= year <= datetime.now().year:
        raise ValueError("Invalid year or trade flow")
    if not isinstance(key,str) or not key.strip() or "\r" in key or "\n" in key:
        raise ValueError("COMTRADE_API_KEY required for complete final trade data (preview cannot be imported)")
    if hs_revision not in HS_REVISION_CODES or commodity not in HS_REVISION_CODES[hs_revision][1]:
        raise ValueError("Requested HS revision/commodity mapping has not been verified")
    classification, mapping = HS_REVISION_CODES[hs_revision]
    cmd = mapping[commodity]
    params = {
        "reporterCode":REPORTERS[reporter],
        "period":year,
        "partnerCode":0,
        "partner2Code":0,
        "cmdCode":cmd,
        "flowCode":"X" if flow=="export" else "M",
        "customsCode":"C00",
        "motCode":0,
        "maxrecords":500,
    }
    url=("https://comtradeapi.un.org/data/v1/get/C/A/"+classification+
         "?"+urllib.parse.urlencode(params))
    body=loader(url,headers={"Ocp-Apim-Subscription-Key":key})
    if not isinstance(body,dict) or body.get("error") not in (None,"","null"):
        raise ValueError("Comtrade final data API reported error")
    rows=body.get("data")
    if not isinstance(rows,list):
        raise ValueError("Invalid Comtrade final data payload")
    try:
        count=int(body.get("count",len(rows)))
    except (ValueError,TypeError) as exc:
        raise ValueError("Comtrade final data count invalid") from exc
    if count!=len(rows) or len(rows)>=500 or count<0:
        raise ValueError("Comtrade final data may be truncated or inconsistent; refusing import")
    if not rows:
        raise ValueError("No verified final trade observation: coverage is unknown, not zero")
    if len(rows)!=1:
        raise ValueError("Cannot identify one unique world/HS6/flow aggregate")
    row=rows[0]
    if not isinstance(row,dict):
        raise ValueError("Invalid Comtrade final record")
    if (str(row.get("reporterCode"))!=str(REPORTERS[reporter]) or
        str(row.get("period"))!=str(year) or
        str(row.get("partnerCode"))!="0" or
        str(row.get("cmdCode")).zfill(6)!=cmd or
        str(row.get("flowCode"))!=("X" if flow=="export" else "M") or
        row.get("classificationCode",classification)!=classification or
        str(row.get("partner2Code",0))!="0" or
        str(row.get("motCode",0))!="0" or
        str(row.get("customsCode","C00"))!="C00"):
        raise ValueError("Comtrade final record differs from exact request dimensions")
    if row.get("aggrLevel") is not None and str(row["aggrLevel"])!="6":
        raise ValueError("Comtrade result is not a 6-digit HS observation")
    # Trade values can legitimately be zero; unlike nonnegative commodity spot quotes.
    try:
        value=float(row["primaryValue"])
    except (ValueError,TypeError,KeyError) as exc:
        raise ValueError("Missing numeric trade value") from exc
    if not math.isfinite(value) or value<0:
        raise ValueError("Invalid trade value")
    qty=row.get("netWgt")
    if qty in (None,""):
        qty=None
    else:
        try: qty=float(qty)
        except (TypeError,ValueError) as exc: raise ValueError("Invalid trade net weight") from exc
        if not math.isfinite(qty) or qty<0:
            raise ValueError("Invalid trade net weight")
    return [{
        "reporter":reporter,"partner":None,"flow":flow,
        "period_start":f"{year}-01-01","period_type":"annual",
        "hs_revision":hs_revision,"hs_code":cmd,"value_usd":value,
        "quantity":qty,"quantity_unit":"kg" if qty is not None else None
    }]


def comtrade_preview_diagnostic(reporter, year, *, commodity, flow="export", loader=fetch_json):
    """Connectivity-only preview. Never return values or rows for persistence."""
    if reporter not in REPORTERS or commodity not in COMMODITIES:
        raise ValueError("Unsupported reporter or commodity")
    if type(year) is not int or flow not in ("export","import") or not 1990<=year<=datetime.now().year:
        raise ValueError("Invalid preview parameters")
    query=urllib.parse.urlencode({
        "reporterCode":REPORTERS[reporter],"period":year,"partnerCode":0,
        "cmdCode":COMMODITIES[commodity][2],
        "flowCode":"X" if flow=="export" else "M",
        "maxrecords":10
    })
    result=loader("https://comtradeapi.un.org/public/v1/preview/C/A/"+
                  COMTRADE_CLASSIFICATION+"?"+query)
    if not isinstance(result,dict) or not isinstance(result.get("data"),list):
        raise ValueError("Preview API is not available")
    return {
        "status":"preview_only_not_importable",
        "rows_returned":len(result["data"]),
        "warning":"Preview can be incomplete; count=0 does NOT imply zero trade."
    }

def eia_spot(series, *, start, end, key, loader=fetch_json):
    """EIA Brent and WTI spot USD/barrel; EIA requires api_key query parameter."""
    if series not in EIA_SERIES or not key:
        raise ValueError("Approved EIA series and EIA_API_KEY required")
    for value in (start, end):
        datetime.strptime(value, "%Y-%m-%d")
    if start > end:
        raise ValueError("Invalid date range")
    query = urllib.parse.urlencode({
        "frequency": "daily", "data[0]": "value", "facets[series][]": series,
        "start": start, "end": end, "api_key": key, "sort[0][column]": "period",
        "sort[0][direction]": "asc", "offset": 0, "length": 5000
    })
    response = loader("https://api.eia.gov/v2/petroleum/pri/spt/data/?" + query)
    payload = response.get("response", {})
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("Unexpected EIA response")
    if int(payload.get("total", len(rows))) > len(rows):
        raise ValueError("EIA result is truncated; narrow the date window")
    dates = set()
    result = []
    for row in rows:
        date = row.get("period")
        if row.get("series") != series or not isinstance(date, str) or not start <= date <= end:
            raise ValueError("EIA response did not match requested series/date")
        datetime.strptime(date, "%Y-%m-%d")
        if date in dates:
            raise ValueError("Duplicate date in EIA response")
        dates.add(date)
        if row.get("value") in (None, ""):
            continue
        result.append((date, finite_price(row["value"])))
    return result


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "p": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _column_index(ref):
    letters = re.match(r"[A-Z]+", ref)
    if not letters:
        raise ValueError("Malformed XLSX cell reference")
    n = 0
    for ch in letters.group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def monthly_workbook(raw):
    """Read World Bank 'Monthly Prices' XLSX without third-party packages.

    Yields worksheet rows; preserves source names and numeric values. Refuses large
    sheets and non-standard workbook layouts rather than silently guessing headers.
    """
    if len(raw) > MAX_RESPONSE or not zipfile.is_zipfile(BytesIO(raw)):
        raise ValueError("Expected a bounded XLSX workbook")
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        def xml(name, max_size=16_000_000):
            info = archive.getinfo(name)
            if info.file_size > max_size:
                raise ValueError("Oversized XLSX member")
            return ET.fromstring(archive.read(name))
        book = xml("xl/workbook.xml")
        sheets = book.find("m:sheets", _NS)
        found = next((s for s in sheets if s.attrib.get("name") == "Monthly Prices"), None) if sheets is not None else None
        if found is None:
            raise ValueError("Monthly Prices sheet not found")
        rel = found.attrib.get("{" + _NS["r"] + "}id")
        relations = xml("xl/_rels/workbook.xml.rels")
        target = next((el.attrib.get("Target") for el in relations
                      if el.attrib.get("Id") == rel), None)
        if not target or ".." in target:
            raise ValueError("Unsafe XLSX sheet relationship")
        name = ("xl/" + target.lstrip("/").removeprefix("xl/"))
        root = xml(name)
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            sst = xml("xl/sharedStrings.xml")
            for item in sst.findall("m:si", _NS):
                strings.append("".join(el.text or "" for el in item.iter("{" + _NS["m"] + "}t")))
        output = []
        sheet_data = root.find("m:sheetData", _NS)
        if sheet_data is None:
            raise ValueError("Missing worksheet content")
        for node in sheet_data.findall("m:row", _NS):
            cells = {}
            for cell in node.findall("m:c", _NS):
                col = _column_index(cell.attrib.get("r", ""))
                kind = cell.attrib.get("t")
                v = cell.findtext("m:v", default=None, namespaces=_NS)
                if kind == "s" and v is not None:
                    v = strings[int(v)]
                elif kind == "inlineStr":
                    v = "".join(t.text or "" for t in cell.iter("{" + _NS["m"] + "}t"))
                elif kind == "n" or kind is None:
                    try:
                        v = float(v) if v is not None else None
                    except (ValueError, TypeError):
                        pass
                if v is not None:
                    cells[col] = v
            if cells:
                output.append(cells)
        return output


def pink_sheet_rows(raw, column):
    """Explicit source column; no inferred commodity/benchmark mapping."""
    if not isinstance(column, str) or not column.strip():
        raise ValueError("Specify an exact Pink Sheet column")
    rows = monthly_workbook(raw)
    header = next(((i, next((k for k, v in row.items() if str(v).strip() == column), None))
                   for i, row in enumerate(rows[:12])
                   if column in [str(v).strip() for v in row.values()]), None)
    if header is None or header[1] is None:
        raise ValueError("Pink Sheet column not found; inspect workbook headers")
    idx, col = header
    result = []
    seen = set()
    for row in rows[idx+1:]:
        date = row.get(0)
        if not isinstance(date, str):
            continue
        match = re.fullmatch(r"(\d{4})M(\d{2})", date.strip())
        if not match:
            continue
        year, month = map(int, match.groups())
        if not 1 <= month <= 12:
            raise ValueError("Invalid Pink Sheet month")
        day = f"{year:04d}-{month:02d}-01"
        value = row.get(col)
        if value in (None, "", "..", "n.a."):
            continue
        if day in seen:
            raise ValueError("Duplicate month in Pink Sheet")
        seen.add(day)
        result.append((day, finite_price(value)))
    if not result:
        raise ValueError("No monthly prices found")
    return result


def pink_sheet_columns(raw):
    rows = monthly_workbook(raw)
    for row in rows[:12]:
        if row.get(0) in ("Month", "Date", None) and len(row) > 5:
            return [str(v) for col, v in sorted(row.items()) if col > 0 and isinstance(v, str)]
    raise ValueError("Could not locate commodity columns")
