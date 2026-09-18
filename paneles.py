import os, requests
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
    "DJX": ["DJX", "DIA", "DJI"],
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
        return round(float(px["Close"].dropna().iloc[-1]), 2)
    except Exception:
        return None

def gex_niveles(tk, fecha):
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
    return out

def _pick(df, names):
    for n in names:
        if n in df.columns:
            return pd.to_numeric(df[n], errors="coerce").fillna(0)
    return pd.Series(0.0, index=df.index)

def _fetch_strikes(tk, fecha):
    raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/spot-exposures/strike",
              {"date": str(fecha)})
    if not isinstance(raw, list) or not raw:
        raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/greek-exposure/strike",
                  {"date": str(fecha)})
    if not isinstance(raw, list) or not raw:
        raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/greek-exposure-by-strike",
                  {"date": str(fecha)})
    return pd.DataFrame(raw if isinstance(raw, list) else [])

def gex_strikes(tk, fecha, spot=None):
    ticks = UW_ALIAS.get(tk, [tk])
    df = pd.DataFrame()
    used = tk
    for cand in ticks:
        df = _fetch_strikes(cand, fecha)
        if not df.empty:
            used = cand
            break
    if df.empty:
        return df
    df.attrs["uw_ticker"] = used
    if "strike" not in df.columns:
        return pd.DataFrame()
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df = df.dropna(subset=["strike"])
    df["call_gex"] = _pick(df, (
        "call_gamma_oi", "call_gex", "call_gamma", "gex_call",
        "call_gamma_vol", "gamma",
    ))
    df["put_gex"] = _pick(df, (
        "put_gamma_oi", "put_gex", "put_gamma", "gex_put", "put_gamma_vol",
    )).abs()
    if spot:
        near = df[(df["strike"] >= spot * 0.985) & (df["strike"] <= spot * 1.015)]
        if near.empty or float((near["call_gex"] + near["put_gex"]).abs().sum()) == 0:
            near = df[(df["strike"] >= spot * 0.97) & (df["strike"] <= spot * 1.03)]
        if not near.empty:
            df = near
    return df.sort_values("strike")

def oi_vol(tk, fecha):
    d = get(f"https://api.unusualwhales.com/api/stock/{tk}/options-volume", {"date": str(fecha)})
    if isinstance(d, list) and d:
        d = d[-1]
    return d if isinstance(d, dict) else {}

def darkpool(tk):
    d = get(f"https://api.unusualwhales.com/api/darkpool/{tk}", {"limit": 200})
    df = pd.DataFrame(d if isinstance(d, list) else [])
    if df.empty:
        return df
    for c in ("price", "size", "premium", "notional"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
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
    vacio = (
        df is None or df.empty or
        float((df.get("call_gex", 0).abs() + df.get("put_gex", 0).abs()).sum()) == 0
    )
    if vacio:
        ax.set_title(f"{tk}{extra}  API sin GEX por strike", color="#e8eef7", loc="left")
        if spot:
            ax.axhline(spot, color="#6ea8ff", ls="--", lw=1.0)
        ax.text(0.02, 0.5, "Sin call_gamma_oi / put_gamma_oi cerca del spot",
                transform=ax.transAxes, color="#8b9bb0", fontsize=9)
        return fig
    y = df["strike"].values
    call = df["call_gex"].fillna(0).values
    put = -df["put_gex"].fillna(0).abs().values
    h = max((np.max(y) - np.min(y)) / max(len(y), 1) * 0.7, 0.15)
    ax.barh(y, call, color="#2ecc71", height=h, label="Call GEX (OI)")
    ax.barh(y, put, color="#9b59b6", height=h, label="Put GEX (OI)")
    if spot:
        ax.axhline(spot, color="#6ea8ff", ls="--", lw=1.1, label=f"Spot {spot:.2f}")
    lo, hi = float(np.min(y)), float(np.max(y))
    def linea(v, c, n):
        if v is None:
            return
        if lo <= v <= hi:
            ax.axhline(v, color=c, ls=":", lw=1.0)
            ax.text(ax.get_xlim()[1], v, f" {n} {v:.2f}", color=c, fontsize=7, va="center")
    linea(niv.get("gamma_flip"), "#1aa3a3", "QF")
    linea(niv.get("gamma_magnet"), "#d4af37", "MAG")
    linea(niv.get("call_wall"), "#2ecc71", "CW")
    linea(niv.get("put_wall"), "#e74c3c", "PW")
    pad = (hi - lo) * 0.08 or 1
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_title(f"{tk}{extra}  perfil GEX  (spot ±3%)", color="#e8eef7", loc="left", fontsize=10)
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

def fig_dp(tk, df):
    fig, ax = plt.subplots(figsize=(7.2, 3.2), facecolor="#0b1220")
    ax.set_facecolor("#0b1220")
    ax.tick_params(colors="#e8eef7", labelsize=8)
    ax.grid(True, color="#1d2a3d", alpha=0.35)
    if df is None or df.empty or "price" not in df.columns:
        ax.set_title(f"{tk}  dark pool sin datos", color="#e8eef7", loc="left")
        return fig
    size = df.get("notional", df.get("premium", df.get("size", 1)))
    df = df.copy()
    df["n"] = pd.to_numeric(size, errors="coerce").fillna(0)
    df = df[(df["price"] > 0) & (df["n"] > 0)]
    if df.empty:
        ax.set_title(f"{tk}  dark pool vacío", color="#e8eef7", loc="left")
        return fig
    cap = df["n"].quantile(0.92)
    df["n"] = df["n"].clip(upper=cap)
    bins = pd.cut(df["price"], bins=min(18, max(6, df["price"].nunique())))
    g = df.groupby(bins, observed=False)["n"].sum()
    mid = [i.mid for i in g.index]
    ht = (mid[1] - mid[0]) * 0.7 if len(mid) > 1 else 0.3
    ax.barh(mid, g.values, height=ht, color="#d4af37", alpha=0.85)
    ax.set_title(f"{tk}  dark pool (prints recientes, no el día entero)",
                 color="#e8eef7", loc="left", fontsize=10)
    ax.set_xlabel("Notional", color="#8b9bb0")
    ax.set_ylabel("Precio", color="#8b9bb0")
    fig.tight_layout()
    return fig
