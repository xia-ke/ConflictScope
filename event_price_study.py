"""P3 descriptive event-window analysis for ConflictScope.

Daily spot observations only; no synthetic trading days, forward-fill,
impact assertions or fictitious statistical significance.
"""
import bisect
import math
import sqlite3
from datetime import date, timedelta

import geoeconomics as geo
import event_data

WINDOW_CHOICES = (5, 10, 20, 30)
MAX_EVENT_ALIGN_DAYS = 7
BASIS = ("impact", "occurred", "announced", "effective", "published")


class MissingStudyEntity(LookupError):
    pass


def _date(s):
    if not isinstance(s, str) or len(s) < 10:
        raise ValueError("Invalid event date")
    return date.fromisoformat(s[:10])


def _anchor(event, basis):
    if basis not in BASIS:
        raise ValueError("Invalid event date basis")
    fields = {
        "occurred": ("occurred_at", "announced_at", "effective_at", "published_at"),
        "announced": ("announced_at", "occurred_at", "effective_at", "published_at"),
        "effective": ("effective_at", "announced_at", "occurred_at", "published_at"),
        "published": ("published_at", "announced_at", "occurred_at", "effective_at"),
        "impact": (
            ("effective_at", "announced_at", "occurred_at", "published_at")
            if event["event_type"] in ("sanction", "embargo", "trade_policy")
            else ("occurred_at", "effective_at", "announced_at", "published_at")
        )
    }[basis]
    for field in fields:
        if event[field]:
            return _date(event[field]), field, field != fields[0]
    raise ValueError("Event does not have a valid dated anchor")


def _quotes(conn, series, date1, date2):
    return [(str(r["observed_at"])[:10], float(r["value"]))
            for r in conn.execute(
                """SELECT observed_at,value FROM price_observations
                   WHERE series_id=? AND observed_at>=? AND observed_at<=?
                   ORDER BY observed_at""",
                (series, date1, date2))]


def _series(conn, series_id):
    item = conn.execute(
        """SELECT p.id,p.commodity_id,p.benchmark,p.currency,p.unit,
                  p.frequency,p.price_type,s.name AS source_name,s.homepage_url AS source_url
           FROM price_series p JOIN data_sources s ON s.id=p.source_id
           WHERE p.id=?""", (series_id,)).fetchone()
    if item is None:
        raise MissingStudyEntity("Price series not found")
    return dict(item)


def _event(conn, event_id):
    event = conn.execute("""SELECT e.*,r.reviewer,r.reviewed_at,r.approval_state
        FROM events e JOIN event_reviews r ON r.event_id=e.id
        WHERE e.id=? AND r.approval_state='approved'""",(event_id,)).fetchone()
    if event is None:
        raise MissingStudyEntity("Reviewed event not found")
    return dict(event)


def _empty(metadata, event, *, status, reason, event_date, date_field, fallback, window):
    return {"status":status,"reason":reason,"series":metadata,
            "event":event,"event_date":event_date.isoformat(),
            "anchor_field":date_field,"date_fallback":fallback,"window":window,
            "points":[],"returns":{}, "warnings":["Insufficient data; no prices fabricated."]}


def study(event_id, series_id, *, basis="impact", window=20, path=geo.DEFAULT_DB):
    """Compare P[-1] to P[t+0/+1/+5/+20] on actual quoted dates.

    Baseline: last reported quote strictly BEFORE the event calendar date.
    t=0: first quoted date on/after event date, max seven calendar days later.
    Returns are cumulative percentages (not abnormal returns), indexed by
    *observed trading sessions* rather than calendar days.
    """
    if window not in WINDOW_CHOICES or basis not in BASIS:
        raise ValueError("Invalid window or date basis")
    if not isinstance(event_id,str) or not isinstance(series_id,str) or not event_id or not series_id or len(event_id)>128 or len(series_id)>128:
        raise ValueError("Invalid event/price identifier")
    with geo.connect(path, readonly=True) as conn:
        conn.row_factory=sqlite3.Row
        meta=_series(conn,series_id)
        event=_event(conn,event_id)
        link=conn.execute("""SELECT 1 FROM event_links WHERE event_id=? AND commodity_id=?
                              LIMIT 1""",(event_id,meta["commodity_id"])).fetchone()
        if not link:
            raise ValueError("No documented event-to-commodity linkage for this series")
        when,field,fallback=_anchor(event,basis)
        event_out={k:event[k] for k in (
            "id","title","event_type","verification_status","source_url",
            "occurred_at","announced_at","effective_at","published_at","date_precision"
        )}
        event_out.update({
            "reviewed_at":event["reviewed_at"],
            "research_ai_assisted":event["reviewer"].startswith("AI-assisted"),
            "editor_reviewed":not event["reviewer"].startswith("AI-assisted")
        })
        if meta["frequency"]!="daily" or meta["price_type"]!="spot":
            return _empty(meta,event_out,status="unsupported_frequency",
                          reason="Event-day windows require daily spot observations; monthly means cannot resolve event-day reaction.",
                          event_date=when,date_field=field,fallback=fallback,window=window)
        # +/- ample buffer to allow normal exchange holidays and all window points.
        start=(when-timedelta(days=220)).isoformat()
        end=(when+timedelta(days=120)).isoformat()
        quotes=_quotes(conn,series_id,start,end)
        if not quotes:
            return _empty(meta,event_out,status="insufficient_data",
                          reason="No observations overlap event window",event_date=when,
                          date_field=field,fallback=fallback,window=window)
        if any(not math.isfinite(v) or v<=0 for _,v in quotes):
            raise ValueError("Invalid stored spot price")
        labels=[d for d,_ in quotes]
        event_str=when.isoformat()
        i=bisect.bisect_left(labels,event_str)
        if i==0 or i>=len(quotes):
            return _empty(meta,event_out,status="insufficient_data",
                          reason="Missing event/post-event quote or prior baseline",event_date=when,
                          date_field=field,fallback=fallback,window=window)
        first=date.fromisoformat(quotes[i][0])
        before=date.fromisoformat(quotes[i-1][0])
        if (first-when).days>MAX_EVENT_ALIGN_DAYS or (when-before).days>MAX_EVENT_ALIGN_DAYS:
            return _empty(meta,event_out,status="insufficient_data",
                          reason="Nearest price observations exceed seven-day alignment tolerance",
                          event_date=when,date_field=field,fallback=fallback,window=window)
        baseline=quotes[i-1][1]
        # A long missing-data gap is NOT a normal trading suspension.
        # Do not describe observations months later as t+1 trading day.
        lo=max(0,i-window)
        hi=min(len(quotes)-1,i+window)
        for j in range(i-1,lo-1,-1):
            if (date.fromisoformat(quotes[j+1][0])-date.fromisoformat(quotes[j][0])).days > MAX_EVENT_ALIGN_DAYS:
                lo=j+1
                break
        for j in range(i+1,hi+1):
            if (date.fromisoformat(quotes[j][0])-date.fromisoformat(quotes[j-1][0])).days > MAX_EVENT_ALIGN_DAYS:
                hi=j-1
                break
        points=[]
        for j in range(lo,hi+1):
            day,value=quotes[j]
            points.append({"offset":j-i,"date":day,"price":value,
                           "cumulative_pct":round((value/baseline-1)*100,6)})
        periods={}
        for k in (0,1,5,10,20,30):
            if k>window:
                continue
            if i+k>hi:
                periods[str(k)]=None
            else:
                d,p=quotes[i+k]
                periods[str(k)]={"trading_offset":k,"date":d,"price":p,
                                  "change_pct":round((p/baseline-1)*100,6)}
        pre_price=quotes[i-window-1][1] if i-window-1>=0 and i-window-1>=lo-1 else None
        warnings=[
            "Descriptive before/after price changes, NOT abnormal returns or proven causal effects.",
            "Event dates are usually day-precision; announcement time vs market close is unknown.",
            "t=0 is first observed market quote on/after event; baseline is last quote strictly before event."
        ]
        if fallback:
            warnings.append("Requested date basis missing: used an explicitly identified fallback field.")
        if any(periods[k] is None for k in periods):
            warnings.append("At least one post-event horizon is unavailable or interrupted by a >7-day price gap; missing values remain null.")
        return {
            "status":"ok","series":meta,"event":event_out,
            "event_date":event_str,"anchor_field":field,"date_fallback":fallback,
            "event_trading_date":quotes[i][0], "baseline_date":quotes[i-1][0],
            "baseline_price":baseline,"window":window,
            "window_unit":"observed_trading_sessions",
            "pre_window_change_pct":round((quotes[i-1][1]/pre_price-1)*100,6) if pre_price else None,
            "points":points,"returns":periods,"warnings":warnings
        }


def compare(*, country, series_id, basis="impact", window=20, limit=30, path=geo.DEFAULT_DB):
    """Compare dated *eligible* events for a commodity, excluding unlinked events.

    Rates are descriptive, not independent samples or independent treatment effects.
    """
    if basis not in BASIS or window not in WINDOW_CHOICES or not isinstance(limit,int) or limit<1 or limit>50:
        raise ValueError("Invalid comparison settings")
    if not isinstance(country,str) or len(country)!=3 or not country.isupper():
        raise ValueError("Invalid ISO3 country")
    with geo.connect(path, readonly=True) as conn:
        conn.row_factory=sqlite3.Row
        meta=_series(conn,series_id)
        rows=conn.execute("""SELECT e.id FROM events e
            JOIN event_reviews r ON r.event_id=e.id AND r.approval_state='approved'
            WHERE EXISTS (SELECT 1 FROM event_links cl WHERE cl.event_id=e.id AND cl.country_id=?)
              AND EXISTS (SELECT 1 FROM event_links ml WHERE ml.event_id=e.id AND ml.commodity_id=?)
            ORDER BY e.id LIMIT ?""",(country,meta["commodity_id"],limit)).fetchall()
    results=[]
    for row in rows:
        doc=study(row["id"],series_id,basis=basis,window=window,path=path)
        results.append({"event":doc["event"],"event_date":doc["event_date"],
                        "anchor_field":doc["anchor_field"],"status":doc["status"],
                        "baseline_date":doc.get("baseline_date"),
                        "returns":doc["returns"],"reason":doc.get("reason")})
    results.sort(key=lambda x:(x["event_date"],x["event"]["id"]))
    return {"country":country,"series":meta,"basis":basis,"window":window,
            "events":results,"count":len(results),
            "note":"Descriptive event windows. Multiple overlapping events, common shocks and differing information times prevent causal interpretation."}
