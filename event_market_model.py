"""Optional benchmark-relative market model for event studies.

OLS on strictly overlapping *daily log returns* in -90..-21 pre-event
target trading sessions. Report CAR(log points) without p-values or causal
claims. No carry-forward for missing quotes; cannot silently use in-sample
event returns to fit the counterfactual.
"""
import math
import sqlite3
from datetime import date, timedelta

import geoeconomics as geo

MIN_ESTIMATION_OBSERVATIONS=30
PRE_START=-90
PRE_END=-21
GAP_LIMIT_DAYS=7


def _market_prices(conn, series, start, end):
    return {row[0]:float(row[1]) for row in conn.execute(
        """SELECT observed_at,value FROM price_observations
           WHERE series_id=? AND observed_at>=? AND observed_at<=?
           ORDER BY observed_at""",(series,start,end))}


def estimate(result, control_series_id, path=geo.DEFAULT_DB):
    """Add model-adjusted CAR when a suitable independent daily control exists."""
    if result["status"]!="ok":
        return {"status":"unavailable","reason":"Descriptive event window must be available first"}
    target=result["series"]
    if not isinstance(control_series_id,str) or not control_series_id or len(control_series_id)>128:
        raise ValueError("Invalid control series ID")
    if target["id"]==control_series_id:
        raise ValueError("Target and control series must differ")
    event_date=date.fromisoformat(result["event_trading_date"])
    earliest=(event_date-timedelta(days=220)).isoformat()
    latest=(event_date+timedelta(days=120)).isoformat()
    with geo.connect(path,readonly=True) as conn:
        conn.row_factory=sqlite3.Row
        control=conn.execute(
            """SELECT id,benchmark,commodity_id,currency,unit,frequency,price_type
               FROM price_series WHERE id=?""",(control_series_id,)).fetchone()
        if control is None:
            raise ValueError("Control price series not found")
        control=dict(control)
        if control["frequency"]!="daily" or control["price_type"]!="spot" or control["currency"]!=target["currency"]:
            raise ValueError("Control must be daily spot series in the same currency")
        target_prices=_market_prices(conn,target["id"],earliest,latest)
        control_prices=_market_prices(conn,control_series_id,earliest,latest)
    if any(not math.isfinite(v) or v<=0 for v in list(target_prices.values())+list(control_prices.values())):
        raise ValueError("Invalid stored market price")
    dates=sorted(target_prices)
    if result["event_trading_date"] not in target_prices:
        raise ValueError("Event trading quote disappeared")
    idx=dates.index(result["event_trading_date"])
    estimates=[]
    def paired_return(j):
        if j<1 or j>=len(dates):
            return None
        d,previous=dates[j],dates[j-1]
        if d not in control_prices or previous not in control_prices:
            return None
        if (date.fromisoformat(d)-date.fromisoformat(previous)).days>GAP_LIMIT_DAYS:
            return None
        return (math.log(target_prices[d]/target_prices[previous]),
                math.log(control_prices[d]/control_prices[previous]))
    for j in range(max(1,idx+PRE_START),max(1,idx+PRE_END+1)):
        pair=paired_return(j)
        if pair is not None:
            estimates.append(pair)
    n=len(estimates)
    if n<MIN_ESTIMATION_OBSERVATIONS:
        return {"status":"insufficient_estimation_data",
                "reason":f"Only {n} overlapping pre-event returns; requires at least 30 in -90..-21.",
                "control":control,"estimation_count":n}
    x=[v[1] for v in estimates]
    y=[v[0] for v in estimates]
    mx=sum(x)/n;my=sum(y)/n
    variance=sum((v-mx)**2 for v in x)
    if variance<1e-12:
        return {"status":"insufficient_estimation_data",
                "reason":"Control series has insufficient pre-event return variation.",
                "control":control,"estimation_count":n}
    beta=sum((a-mx)*(b-my) for a,b in zip(x,y))/variance
    alpha=my-beta*mx
    residuals={}
    for j in range(idx,min(idx+result["window"]+1,len(dates))):
        pair=paired_return(j)
        residuals[j-idx]=pair[0]-(alpha+beta*pair[1]) if pair is not None else None
    car={}
    for horizon in (0,1,5,10,20,30):
        if horizon>result["window"]:
            continue
        if any(residuals.get(k) is None for k in range(horizon+1)):
            car[str(horizon)]=None
        else:
            car[str(horizon)]=round(100*sum(residuals[k] for k in range(horizon+1)),6)
    return {"status":"ok","method":"OLS log returns (daily)",
            "control":control,"estimation_window":"[-90,-21] target trading sessions",
            "estimation_count":n,"alpha_log":round(alpha,9),
            "beta":round(beta,6),
            "cumulative_abnormal_log_return_pct":car,
            "warning":"Model-dependent benchmark-relative CAR, not a causal effect, confidence interval or significance test."}
