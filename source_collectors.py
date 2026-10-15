"""GDELT and optional ReliefWeb discovery for ConflictScope.

External reports are leads, not independently verified conflict events.
Standard-library only; network failures never block existing RSS fallback.
"""
import argparse
import gzip
import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

UTC = timezone.utc
MAX_BYTES = 3_000_000
HEADERS = {"User-Agent": "ConflictScope/2.1 (+https://github.com/xia-ke/ConflictScope)", "Accept": "application/json"}


def safe_link(value):
    if not isinstance(value, str):
        return ""
    try:
        p = urllib.parse.urlsplit(value)
        return value if p.scheme in ("https", "http") and p.hostname and not (p.username or p.password) else ""
    except ValueError:
        return ""


def timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        if re.fullmatch(r"\d{14}", value):
            dt = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        else:
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def get_json(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=12) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Response too large")
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
        if len(raw) > MAX_BYTES:
            raise ValueError("Decompressed response too large")
    return json.loads(raw)


def normalize(title, url, source, published, provider, kind):
    title = html.unescape(re.sub(r"<[^>]*>", "", str(title or ""))).strip()
    url = safe_link(url)
    published = timestamp(published)
    if not (title and url and published):
        return None
    return {"title": title[:350], "url": url, "source": str(source or provider)[:120],
            "published": published, "kind": kind, "provider": provider}


def gdelt(country, days=7, limit=75, loader=get_json):
    params = urllib.parse.urlencode({
        "query": str(country["query"]), "mode": "artlist", "format": "json",
        "timespan": f"{min(max(days, 1), 90)}d",
        "maxrecords": min(max(limit, 1), 250), "sort": "datedesc"
    })
    response = loader("https://api.gdeltproject.org/api/v2/doc/doc?" + params)
    if not isinstance(response, dict) or not isinstance(response.get("articles", []), list):
        raise ValueError("Unexpected GDELT response")
    result = []
    for entry in response.get("articles", []):
        if isinstance(entry, dict):
            item = normalize(entry.get("title"), entry.get("url"), entry.get("domain"),
                             entry.get("seendate"), "GDELT", "media")
            if item:
                result.append(item)
    return result


def reliefweb(country, days=30, limit=40, appname=None, loader=get_json):
    appname = appname or os.getenv("RELIEFWEB_APPNAME", "")
    if not appname:
        raise ValueError("ReliefWeb requires an approved RELIEFWEB_APPNAME")
    params = urllib.parse.urlencode({
        "appname": appname, "limit": min(max(limit, 1), 100), "preset": "latest",
        "query[value]": country["en"],
        "fields[include][]": ["title", "url", "date.created", "date.original", "source.name"],
    }, doseq=True)
    response = loader("https://api.reliefweb.int/v2/reports?" + params)
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise ValueError("Unexpected ReliefWeb response")
    result = []
    for entry in response["data"]:
        if not isinstance(entry, dict):
            continue
        fields = entry.get("fields") or {}
        date = fields.get("date") or {}
        sources = fields.get("source") or []
        source = sources[0].get("name") if isinstance(sources, list) and sources and isinstance(sources[0], dict) else "ReliefWeb"
        item = normalize(fields.get("title"), fields.get("url"), source,
                         date.get("original") or date.get("created"), "ReliefWeb", "humanitarian")
        if item:
            result.append(item)
    return result


def collect(country, days=30, include_reliefweb=None):
    providers, failed, articles = [], [], []
    if include_reliefweb is None:
        include_reliefweb = bool(os.getenv("RELIEFWEB_APPNAME"))
    jobs = [("GDELT", lambda: gdelt(country, days))]
    if include_reliefweb:
        jobs.append(("ReliefWeb", lambda: reliefweb(country, days)))
    for provider, fetch in jobs:
        try:
            articles.extend(fetch())
            providers.append(provider)
        except (OSError, ValueError, TimeoutError):
            failed.append(provider)
    return articles, providers, failed


def main():
    parser = argparse.ArgumentParser(description="Fetch structured conflict reports")
    parser.add_argument("--country", default="UKR")
    parser.add_argument("--days", type=int, choices=(7, 30, 90), default=30)
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    config = json.loads((root / "data/conflicts.json").read_text(encoding="utf-8"))
    countries = {country["id"]: country for country in config["countries"]}
    if args.country not in countries:
        parser.error("Unknown country code")
    articles, providers, failed = collect(countries[args.country], args.days)
    payload = {"country": args.country, "providers": providers, "failed_providers": failed,
               "articles": articles, "note": "Discovery results, not verified conflict events."}
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
