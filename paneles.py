import os, requests
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf

API = os.getenv("UW_API_KEY", "")
PARES = [("QQQ", "NDX"), ("SPY", "SPX"), ("DIA", "DJX")]
YMAP = {
    "QQQ": "QQQ", "NDX": "^NDX",
    "SPY": "SPY", "SPX": "^GSPC",
    "DIA": "DIA", "DJX": "^DJI",
    "GLD": "GLD",
}
UW_ALIAS = {
    "NDX": ["NDX", "QQQ"],
    "SPX": ["SPX", "SPXW"],
    "DJX": ["DJX", "DIA"],
}

def get(url, params=None):
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {API}", "Accept": "application/json"},
            params=params or {}, timeout=25,
        )
        if r.status_code != 200:
            return None
        p = r.json()
        return p.get("data") if isinstance(p, dict) else p
    except Exception:
        return None

def fnum(x):
    try:
        return None if x in (None, "") else float(x)
    except Exception:
        return None

def last_price(tk):
    try:
        px = yf.download(YMAP.get(tk, tk), period="5d", interval="5m",
                         progress=False, auto_adjust=True)
        if px is None or px.empty:
            return None
        if isinstance(px.columns, pd.MultiIndex):
            px.columns = px.columns.get_level_values(0)
        last = float(px["Close"].dropna().iloc[-1])
        if tk == "DJX":
            last = last / 100.0
        return round(last, 2)
    except Exception:
        return None

def cerca(v, spot, pct=0.04):
    if v is None or not spot:
        return None
    try:
        v = float(v)
    except Exception:
        return None
    if abs(v - spot) / max(abs(spot), 1) > pct:
        return None
    return v

def gex_niveles(tk, fecha, spot=None):
    out = {}
    for src in ("oi", "vol"):
        d = get(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
                {"date": str(fecha), "source": src})
        if not isinstance(d, dict):
            continue
        for k in ("call_wall", "put_wall", "gamma_flip", "gamma_magnet"):
            v = fnum(d.get(k))
            if v is not None and k not in out:
                out[k] = v
    if spot:
        limpio = {}
        for k, pct in (("gamma_flip", 0.02), ("call_wall", 0.04),
                       ("put_wall", 0.04), ("gamma_magnet", 0.04)):
            v = cerca(out.get(k), spot, pct)
            if v is not None:
                limpio[k] = v
        return limpio
    return out

def _pick(df, names):
    for n in names:
        if n in df.columns:
            return pd.to_numeric(df[n], errors="coerce").fillna(0)
    return pd.Series(0.0, index=df.index)

def _fetch_strikes(tk, fecha):
    for path, extra in (
        (f"https://api.unusualwhales.com/api/stock/{tk}/spot-exposures/strike",
         {"date": str(fecha), "source": "oi"}),
        (f"https://api.unusualwhales.com/api/stock/{tk}/spot-exposures/strike",
         {"date": str(fecha)}),
        (f"https://api.unusualwhales.com/api/stock/{tk}/greek-exposure/strike",
         {"date": str(fecha)}),
        (f"https://api.unusualwhales.com/api/stock/{tk}/greek-exposure-by-strike",
         {"date": str(fecha)}),
    ):
        raw = get(path, extra)
        if isinstance(raw, list) and raw:
            return pd.DataFrame(raw)
    return pd.DataFrame()

def gex_strikes(tk, fecha, spot=None):
    ticks = UW_ALIAS.get(tk, [tk])
    df = pd.DataFrame()
    used = tk
    for cand in ticks:
        df = _fetch_strikes(cand, fecha)
        if not df.empty:
            used = cand
            break
    if df.empty or "strike" not in df.columns:
        return pd.DataFrame()
    df.attrs["uw_ticker"] = used
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df = df.dropna(subset=["strike"])
    df["call_gex"] = _pick(df, (
        "call_gamma_oi", "call_gex", "call_gamma", "gex_call", "call_gamma_vol",
    ))
    df["put_gex"] = _pick(df, (
        "put_gamma_oi", "put_gex", "put_gamma", "gex_put", "put_gamma_vol",
    )).abs()
    if spot:
        for lo, hi in ((0.985, 1.015), (0.97, 1.03), (0.96, 1.04)):
            near = df[(df["strike"] >= spot * lo) & (df["strike"] <= spot * hi)]
            if not near.empty and float((near["call_gex"] + near["put_gex"]).abs().sum()) != 0:
                near.attrs["uw_ticker"] = used
                return near.sort_values("strike")
        vac = pd.DataFrame(columns=df.columns)
        vac.attrs["uw_ticker"] = used
        return vac
    return df.sort_values("strike")

def oi_vol(tk, fecha):
    d = get(f"https://api.unusualwhales.com/api/stock/{tk}/options-volume", {"date": str(fecha)})
    if isinstance(d, list) and d:
        d = d[-1]
    return d if isinstance(d, dict) else {}

def darkpool(tk, fecha=None):
    if fecha is None:
        fecha = datetime.now(ZoneInfo("America/New_York")).date()
    a = datetime(fecha.year, fecha.month, fecha.day, 9, 30,
                 tzinfo=ZoneInfo("America/New_York")).astimezone(ZoneInfo("UTC"))
    b = datetime(fecha.year, fecha.month, fecha.day, 16, 5,
                 tzinfo=ZoneInfo("America/New_York")).astimezone(ZoneInfo("UTC"))
    raw = get(f"https://api.unusualwhales.com/api/darkpool/{tk}", {
        "date": str(fecha),
        "newer_than": a.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "older_than": b.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 500,
    })
    df = pd.DataFrame(raw if isinstance(raw, list) else [])
    if df.empty:
        return df
    for c in ("price", "size", "premium", "notional"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    col_t = "executed_at" if "executed_at" in df.columns else "trf_executed_at"
    if col_t in df.columns:
        df["hora"] = pd.to_datetime(df[col_t], utc=True, errors="coerce")
        df = df[df["hora"].notna()]
        df = df[(df["hora"] >= a) & (df["hora"] <= b)]
    return df

def fig_gex(tk, df, niv, spot):
    fig, ax = plt.subplots(figsize=(7.2, 4.4), facecolor="#0b1220")
    ax.set_facecolor("#0b1220")
    ax.tick_params(colors="#e8eef7", labelsize=8)
    ax.grid(True, color="#1d2a3d", alpha=0.4)
    for s in ax.spines.values():
        s.set_color("#1d2a3d")
    tag = df.attrs.get("uw_ticker") if df is not None else None
    extra = f"  ({tag})" if tag and tag != tk else ""
    if spot:
        ax.set_ylim(spot * 0.96, spot * 1.04)
    vacio = (
        df is None or df.empty or
        float((df.get("call_gex", pd.Series(dtype=float)).abs()
               + df.get("put_gex", pd.Series(dtype=float)).abs()).sum() or 0) == 0
    )
    if vacio:
        ax.set_title(f"{tk}{extra}  sin GEX cerca del spot", color="#e8eef7", loc="left")
        if spot:
            ax.axhline(spot, color="#6ea8ff", ls="--", lw=1.0, label=f"Spot {spot:.2f}")
            ax.legend(facecolor="#121b2c", labelcolor="#e8eef7", fontsize=7)
        ax.text(0.03, 0.5,
                "No hay call_gamma_oi / put_gamma_oi ±4% del spot.\nNo se pinta la cadena lejana.",
                transform=ax.transAxes, color="#8b9bb0", fontsize=8)
        return fig
    y = df["strike"].values
    call = df["call_gex"].fillna(0).values
    put = -df["put_gex"].fillna(0).abs().values
    h = max((np.max(y) - np.min(y)) / max(len(y), 1) * 0.65, (spot or 100) * 0.0015)
    ax.barh(y, call, color="#2ecc71", height=h, label="Call GEX (OI)")
    ax.barh(y, put, color="#9b59b6", height=h, label="Put GEX (OI)")
    if spot:
        ax.axhline(spot, color="#6ea8ff", ls="--", lw=1.15, label=f"Spot {spot:.2f}")
        ax.set_ylim(spot * 0.96, spot * 1.04)
    lo, hi = ax.get_ylim()
    def linea(v, c, n):
        if v is None:
            return
        if lo <= v <= hi:
            ax.axhline(v, color=c, ls=":", lw=1.0)
            ax.text(0.99, v, f" {n} {v:.2f}", transform=ax.get_yaxis_transform(),
                    color=c, fontsize=7, va="center", ha="right")
    linea(niv.get("gamma_flip"), "#1aa3a3", "QF")
    linea(niv.get("gamma_magnet"), "#d4af37", "MAG")
    linea(niv.get("call_wall"), "#2ecc71", "CW")
    linea(niv.get("put_wall"), "#e74c3c", "PW")
    ax.set_title(f"{tk}{extra}  perfil GEX  (spot ±4%)", color="#e8eef7", loc="left", fontsize=10)
    ax.set_xlabel("GEX", color="#8b9bb0")
    ax.set_ylabel("Strike", color="#8b9bb0")
    ax.legend(facecolor="#121b2c", labelcolor="#e8eef7", fontsize=7, loc="lower right")
    fig.tight_layout()
    return fig

def fig_oi(tk, o):
    fig, ax = plt.subplots(figsize=(7.2, 2.4), facecolor="#0b1220")
    ax.set_facecolor("#0b1220")
    ax.tick_params(colors="#e8eef7", labelsize=8)
    vals = [
        float(o.get("call_open_interest") or 0),
        float(o.get("put_open_interest") or 0),
        float(o.get("call_volume") or 0),
        float(o.get("put_volume") or 0),
    ]
    ax.bar(["Call OI", "Put OI", "Call vol", "Put vol"], vals,
           color=["#2ecc71", "#9b59b6", "#5ec8c6", "#e74c3c"])
    ax.set_title(f"{tk}  OI / volumen", color="#e8eef7", loc="left", fontsize=10)
    fig.tight_layout()
    return fig

def fig_dp(tk, df, spot=None):
    fig, ax = plt.subplots(figsize=(7.2, 3.2), facecolor="#0b1220")
    ax.set_facecolor("#0b1220")
    ax.tick_params(colors="#e8eef7", labelsize=8)
    ax.grid(True, color="#1d2a3d", alpha=0.35)
    if df is None or df.empty or "price" not in df.columns:
        ax.set_title(f"{tk}  dark pool sin datos de la sesión", color="#e8eef7", loc="left")
        return fig
    size = df.get("notional", df.get("premium", df.get("size", 1)))
    df = df.copy()
    df["n"] = pd.to_numeric(size, errors="coerce").fillna(0)
    df = df[(df["price"] > 0) & (df["n"] > 0)]
    if spot:
        df = df[(df["price"] >= spot * 0.97) & (df["price"] <= spot * 1.03)]
    if df.empty:
        ax.set_title(f"{tk}  dark pool vacío cerca del spot", color="#e8eef7", loc="left")
        return fig
    cap = df["n"].quantile(0.92)
    df["n"] = df["n"].clip(upper=cap)
    bins = pd.cut(df["price"], bins=min(16, max(6, df["price"].nunique())))
    g = df.groupby(bins, observed=False)["n"].sum()
    mid = [i.mid for i in g.index]
    ht = (mid[1] - mid[0]) * 0.7 if len(mid) > 1 else (spot or 1) * 0.002
    ax.barh(mid, g.values, height=ht, color="#d4af37", alpha=0.85)
    if spot:
        ax.axhline(spot, color="#6ea8ff", ls="--", lw=0.9)
        ax.set_ylim(spot * 0.97, spot * 1.03)
    ax.set_title(f"{tk}  dark pool sesión 9:30–16:00  ({len(df)} prints)",
                 color="#e8eef7", loc="left", fontsize=10)
    ax.set_xlabel("Notional", color="#8b9bb0")
    ax.set_ylabel("Precio", color="#8b9bb0")
    fig.tight_layout()
    return fig
