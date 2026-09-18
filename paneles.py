import os, requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf

API = os.getenv("UW_API_KEY", "")
PARES = [("QQQ", "NDX"), ("SPY", "SPX"), ("IWM", "RUT")]
YMAP = {
    "QQQ": "QQQ", "NDX": "^NDX", "SPY": "SPY", "SPX": "^GSPC",
    "IWM": "IWM", "RUT": "^RUT", "IBIT": "IBIT", "GLD": "GLD",
}

def get(url, params=None):
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {API}", "Accept": "application/json"},
                         params=params or {}, timeout=25)
        if r.status_code != 200: return None
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
    sym = YMAP.get(tk, tk)
    try:
        px = yf.download(sym, period="5d", interval="5m", progress=False, auto_adjust=True)
        if px is None or px.empty: return None
        if isinstance(px.columns, pd.MultiIndex):
            px.columns = px.columns.get_level_values(0)
        return float(px["Close"].dropna().iloc[-1])
    except Exception:
        return None

def gex_niveles(tk, fecha):
    out = {}
    for src in ("oi", "vol"):
        d = get(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
                {"date": str(fecha), "source": src})
        if not isinstance(d, dict): continue
        for k in ("call_wall", "put_wall", "gamma_flip", "gamma_magnet"):
            v = fnum(d.get(k))
            if v is not None and k not in out:
                out[k] = v
    return out

def gex_strikes(tk, fecha, spot=None):
    raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/spot-exposures/strike",
              {"date": str(fecha)})
    if not isinstance(raw, list):
        raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/greek-exposure",
                  {"date": str(fecha)}) or []
    df = pd.DataFrame(raw if isinstance(raw, list) else [])
    if df.empty: return df
    for c in ("strike", "call_gamma", "put_gamma", "call_gex", "put_gex", "gamma"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "call_gex" not in df.columns:
        df["call_gex"] = df.get("call_gamma", 0)
    if "put_gex" not in df.columns:
        df["put_gex"] = df.get("put_gamma", 0)
    df = df.dropna(subset=["strike"])
    if spot:
        df = df[(df["strike"] >= spot * 0.985) & (df["strike"] <= spot * 1.015)]
    return df.sort_values("strike")

def oi_vol(tk, fecha):
    d = get(f"https://api.unusualwhales.com/api/stock/{tk}/options-volume", {"date": str(fecha)})
    if isinstance(d, list) and d: d = d[-1]
    return d if isinstance(d, dict) else {}

def darkpool(tk):
    d = get(f"https://api.unusualwhales.com/api/darkpool/{tk}", {"limit": 200})
    df = pd.DataFrame(d if isinstance(d, list) else [])
    if df.empty: return df
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
    if df is None or df.empty:
        ax.set_title(f"{tk}  sin GEX strike", color="#e8eef7", loc="left")
        return fig
    y = df["strike"].values
    call = df["call_gex"].fillna(0).values
    put = -abs(df["put_gex"].fillna(0).values)
    ax.barh(y, call, color="#2ecc71", height=0.55, label="Call GEX")
    ax.barh(y, put, color="#9b59b6", height=0.55, label="Put GEX")
    if spot:
        ax.axhline(spot, color="#6ea8ff", ls="--", lw=1.1, label=f"Spot {spot:.2f}")
    qf, mag, cw, pw = niv.get("gamma_flip"), niv.get("gamma_magnet"), niv.get("call_wall"), niv.get("put_wall")
    lo, hi = float(np.min(y)), float(np.max(y))
    def linea(v, c, n):
        if v is None: return
        if lo <= v <= hi:
            ax.axhline(v, color=c, ls=":", lw=1.0)
            ax.text(ax.get_xlim()[1], v, f" {n} {v:.2f}", color=c, fontsize=7, va="center")
    linea(qf, "#1aa3a3", "QF")
    linea(mag, "#d4af37", "MAG")
    linea(cw, "#2ecc71", "CW")
    linea(pw, "#e74c3c", "PW")
    pad = (hi - lo) * 0.08 or 1
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_title(f"{tk}  perfil GEX  (zoom spot ±1.5%)", color="#e8eef7", loc="left", fontsize=10)
    ax.set_xlabel("GEX", color="#8b9bb0")
    ax.set_ylabel("Strike", color="#8b9bb0")
    ax.legend(facecolor="#121b2c", labelcolor="#e8eef7", fontsize=7, loc="lower right")
    fig.tight_layout()
    return fig

def fig_oi(tk, o):
    fig, ax = plt.subplots(figsize=(7.2, 2.4), facecolor="#0b1220")
    ax.set_facecolor("#0b1220")
    ax.tick_params(colors="#e8eef7", labelsize=8)
    vals = [float(o.get("call_open_interest") or 0), float(o.get("put_open_interest") or 0),
            float(o.get("call_volume") or 0), float(o.get("put_volume") or 0)]
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
    bins = pd.cut(df["price"], bins=18)
    g = df.groupby(bins, observed=False)["n"].sum()
    mid = [i.mid for i in g.index]
    ax.barh(mid, g.values, height=(mid[1]-mid[0])*0.7 if len(mid) > 1 else 0.3, color="#d4af37", alpha=0.85)
    ax.set_title(f"{tk}  dark pool (perfil)", color="#e8eef7", loc="left", fontsize=10)
    ax.set_xlabel("Notional", color="#8b9bb0")
    ax.set_ylabel("Precio", color="#8b9bb0")
    fig.tight_layout()
    return fig