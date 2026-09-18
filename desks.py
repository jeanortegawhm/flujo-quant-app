import os
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import requests

API = os.getenv("UW_API_KEY", "")
TZ = ZoneInfo("America/New_York")
H = {"Authorization": f"Bearer {API}", "Accept": "application/json"}

def get(url, params=None):
    try:
        r = requests.get(url, headers=H, params=params or {}, timeout=25)
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

def flow_strike(tk, fecha, spot=None):
    raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/flow-per-strike", {"date": str(fecha)})
    if not isinstance(raw, list) or not raw:
        raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/flow-per-strike-intraday", {"date": str(fecha)})
    df = pd.DataFrame(raw if isinstance(raw, list) else [])
    if df.empty:
        return df
    if "strike" in df.columns:
        df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    for c in ("call_premium", "put_premium", "call_volume", "put_volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    if "call_premium" not in df.columns:
        df["call_premium"] = 0.0
    if "put_premium" not in df.columns:
        df["put_premium"] = 0.0
    df["net"] = df["call_premium"] - df["put_premium"]
    if spot and "strike" in df.columns:
        df = df[(df["strike"] >= spot * 0.96) & (df["strike"] <= spot * 1.04)]
    if "strike" in df.columns:
        df = df.dropna(subset=["strike"]).groupby("strike", as_index=False)[["call_premium", "put_premium", "net"]].sum()
        return df.sort_values("strike")
    return df

def greeks_net(tk, fecha):
    raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/greek-exposure", {"date": str(fecha)})
    row = {}
    if isinstance(raw, list) and raw:
        row = raw[0] if isinstance(raw[0], dict) else {}
    elif isinstance(raw, dict):
        row = raw
    out = {}
    for k in ("call_gamma", "put_gamma", "call_vanna", "put_vanna", "call_charm", "put_charm",
              "call_delta", "put_delta"):
        out[k] = fnum(row.get(k)) or 0.0
    out["net_gamma"] = out["call_gamma"] + out["put_gamma"]
    out["net_vanna"] = out["call_vanna"] + out["put_vanna"]
    out["net_charm"] = out["call_charm"] + out["put_charm"]
    return out

def oi_change(tk, fecha, limit=25):
    raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/oi-change",
              {"date": str(fecha), "limit": limit, "order": "desc"})
    df = pd.DataFrame(raw if isinstance(raw, list) else [])
    if df.empty:
        return df
    for c in ("curr_oi", "last_oi", "oi_diff_plain", "volume", "avg_price"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "oi_diff_plain" not in df.columns and {"curr_oi", "last_oi"} <= set(df.columns):
        df["oi_diff_plain"] = df["curr_oi"] - df["last_oi"]
    return df.head(limit)

def multi_leg(tk, fecha, limit=40):
    a = datetime(fecha.year, fecha.month, fecha.day, 9, 30, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    b = datetime(fecha.year, fecha.month, fecha.day, 16, 5, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    raw = get("https://api.unusualwhales.com/api/option-trades/multi-leg", {
        "ticker_symbol": tk,
        "newer_than": a.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "older_than": b.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": limit,
    })
    return pd.DataFrame(raw if isinstance(raw, list) else [])

def scanner(fecha, min_prem=1_500_000, limit=40):
    a = datetime(fecha.year, fecha.month, fecha.day, 9, 25, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    b = datetime(fecha.year, fecha.month, fecha.day, 16, 5, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    raw = get("https://api.unusualwhales.com/api/option-trades/flow-alerts", {
        "newer_than": a.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "older_than": b.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "min_premium": min_prem,
        "limit": limit,
    })
    if not isinstance(raw, list) or not raw:
        raw = get("https://api.unusualwhales.com/api/market/oi-change",
                  {"date": str(fecha), "limit": limit})
    return pd.DataFrame(raw if isinstance(raw, list) else [])

def fig_flow_strike(tk, df, spot):
    fig, ax = plt.subplots(figsize=(7.4, 4.2), facecolor="#0b1220")
    ax.set_facecolor("#0b1220")
    ax.tick_params(colors="#e8eef7", labelsize=8)
    ax.grid(True, color="#1d2a3d", alpha=0.35)
    if df is None or df.empty or "strike" not in df.columns:
        ax.set_title(f"{tk}  flow por strike vacío", color="#e8eef7", loc="left")
        return fig
    y = df["strike"].values
    call = df["call_premium"].fillna(0).values / 1e6
    put = -df["put_premium"].fillna(0).values / 1e6
    h = max((np.max(y) - np.min(y)) / max(len(y), 1) * 0.6, (spot or 100) * 0.0012)
    ax.barh(y, call, height=h, color="#2ecc71", label="Call $M")
    ax.barh(y, put, height=h, color="#9b59b6", label="Put $M")
    if spot:
        ax.axhline(spot, color="#6ea8ff", ls="--", lw=1.0)
        ax.set_ylim(spot * 0.96, spot * 1.04)
    ax.set_title(f"{tk}  prima por strike (sesión)", color="#e8eef7", loc="left", fontsize=10)
    ax.set_xlabel("$M", color="#8b9bb0")
    ax.legend(facecolor="#121b2c", labelcolor="#e8eef7", fontsize=7)
    fig.tight_layout()
    return fig

def fig_oi_chg(tk, df):
    fig, ax = plt.subplots(figsize=(7.4, 3.4), facecolor="#0b1220")
    ax.set_facecolor("#0b1220")
    ax.tick_params(colors="#e8eef7", labelsize=7)
    if df is None or df.empty:
        ax.set_title(f"{tk}  sin OI change (sale ~6:45 ET)", color="#e8eef7", loc="left")
        return fig
    lab = df.get("option_symbol", df.index.astype(str)).astype(str).str[-15:]
    val = pd.to_numeric(df.get("oi_diff_plain", 0), errors="coerce").fillna(0)
    top = pd.DataFrame({"lab": lab, "v": val}).nlargest(12, "v", keep="all")
    cols = ["#2ecc71" if v >= 0 else "#e74c3c" for v in top["v"]]
    ax.barh(top["lab"], top["v"], color=cols)
    ax.set_title(f"{tk}  Δ OI (posición que se quedó)", color="#e8eef7", loc="left", fontsize=10)
    fig.tight_layout()
    return fig
