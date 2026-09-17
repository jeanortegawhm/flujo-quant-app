import os, requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def _key():
    return os.getenv("UW_API_KEY", "")

def get(url, params=None):
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {_key()}", "Accept": "application/json"},
            params=params or {},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        p = r.json()
        return p.get("data") if isinstance(p, dict) else p
    except Exception:
        return None

def gex_niveles(tk, fecha=None):
    params = {"source": "oi"}
    if fecha:
        params["date"] = str(fecha)
    d = get(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels", params)
    return d if isinstance(d, dict) else {}

def oi_vol(tk, fecha=None):
    params = {}
    if fecha:
        params["date"] = str(fecha)
    d = get(f"https://api.unusualwhales.com/api/stock/{tk}/options-volume", params)
    if isinstance(d, list) and d:
        return d[0] if isinstance(d[0], dict) else {}
    return d if isinstance(d, dict) else {}

def gex_strikes(tk, fecha=None, spot=None):
    params = {}
    if fecha:
        params["date"] = str(fecha)
    if spot:
        params["min_strike"] = round(float(spot) * 0.97, 2)
        params["max_strike"] = round(float(spot) * 1.03, 2)
    rows = None
    for path in ("spot-exposures/strike", "greek-exposure/strike", "flow-per-strike"):
        data = get(f"https://api.unusualwhales.com/api/stock/{tk}/{path}", params)
        if isinstance(data, list) and data:
            rows = data
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    col = next((c for c in ("strike", "strike_price") if c in df.columns), None)
    if not col:
        return pd.DataFrame()
    df["strike"] = pd.to_numeric(df[col], errors="coerce")
    call = pd.to_numeric(df.get("call_gamma_oi", df.get("call_gex", df.get("call_premium", 0))), errors="coerce").fillna(0)
    put = pd.to_numeric(df.get("put_gamma_oi", df.get("put_gex", df.get("put_premium", 0))), errors="coerce").fillna(0)
    df["call_gex"] = call
    df["put_gex"] = -put.abs()
    df["net"] = df["call_gex"] + df["put_gex"]
    df = df.dropna(subset=["strike"]).sort_values("strike")
    if spot:
        df = df[abs(df["strike"] - float(spot)) / max(abs(float(spot)), 1) <= 0.03]
    return df

def darkpool(tk, limit=25):
    data = get(f"https://api.unusualwhales.com/api/darkpool/{tk}", {"limit": limit})
    if not isinstance(data, list):
        data = get("https://api.unusualwhales.com/api/darkpool/recent", {"limit": limit, "ticker_symbol": tk})
    if not isinstance(data, list):
        return pd.DataFrame()
    df = pd.DataFrame(data)
    for c in ("price", "size", "premium", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.head(limit)

def _tema(ax, bg="#0b1220", fg="#e8eef7"):
    ax.set_facecolor(bg)
    ax.tick_params(colors=fg, labelsize=8)
    ax.grid(True, color="#1d2a3d", alpha=0.45)
    for s in ax.spines.values():
        s.set_color("#1d2a3d")

def fig_gex(tk, df, niv, spot=None):
    bg, fg = "#0b1220", "#e8eef7"
    fig, ax = plt.subplots(figsize=(8.6, 5.4), facecolor=bg)
    _tema(ax)
    if df is None or df.empty:
        ax.text(0.5, 0.5, f"Sin perfil GEX · {tk}", ha="center", va="center",
                transform=ax.transAxes, color="#8b9bb0")
        ax.set_title(tk, color=fg, loc="left")
        fig.tight_layout()
        return fig
    ax.barh(df["strike"], df["call_gex"], color="#2ecc71", alpha=0.85, label="Call GEX")
    ax.barh(df["strike"], df["put_gex"], color="#9b59b6", alpha=0.85, label="Put GEX")
    if spot:
        ax.axhline(float(spot), color="#6ea8ff", lw=1.3, ls="--", label=f"Spot {float(spot):.2f}")
    cols = {"call_wall": "#2ecc71", "put_wall": "#e74c3c", "gamma_flip": "#1aa3a3", "gamma_magnet": "#f1c40f"}
    for k, c in cols.items():
        v = niv.get(k)
        if v is None:
            continue
        try:
            v = float(v)
        except Exception:
            continue
        ax.axhline(v, color=c, lw=1.1, ls=":")
        ax.text(ax.get_xlim()[1], v, f" {k.replace('gamma_','')} {v:.2f}", color=c, va="center", fontsize=8)
    ax.set_xlabel("GEX", color=fg)
    ax.set_ylabel("Strike", color=fg)
    ax.set_title(f"{tk}  perfil GEX", color=fg, loc="left")
    ax.legend(facecolor=bg, labelcolor=fg, fontsize=8)
    fig.tight_layout()
    return fig

def fig_oi(tk, o):
    bg, fg = "#0b1220", "#e8eef7"
    fig, axs = plt.subplots(1, 2, figsize=(8.6, 3.4), facecolor=bg)
    for ax in axs:
        _tema(ax)
    def f(x):
        try:
            return float(x)
        except Exception:
            return 0.0
    axs[0].bar(["Call OI", "Put OI"], [f(o.get("call_open_interest")), f(o.get("put_open_interest"))],
               color=["#2ecc71", "#9b59b6"])
    axs[0].set_title("Open interest", color=fg, loc="left")
    axs[1].bar(["Call vol", "Put vol"], [f(o.get("call_volume")), f(o.get("put_volume"))],
               color=["#2ecc71", "#9b59b6"])
    axs[1].set_title("Volumen opciones", color=fg, loc="left")
    fig.suptitle(tk, color=fg, x=0.02, ha="left")
    fig.tight_layout()
    return fig

def fig_dp(tk, dp):
    bg, fg = "#0b1220", "#e8eef7"
    fig, ax = plt.subplots(figsize=(8.6, 3.6), facecolor=bg)
    _tema(ax)
    if dp is None or dp.empty or "price" not in dp.columns:
        ax.text(0.5, 0.5, f"Sin dark pool · {tk}", ha="center", va="center",
                transform=ax.transAxes, color="#8b9bb0")
        fig.tight_layout()
        return fig
    y = dp["premium"] if "premium" in dp.columns else dp.get("size", 1)
    ax.scatter(dp["price"], y, s=40, c="#d4af37", alpha=0.85)
    ax.set_xlabel("Precio", color=fg)
    ax.set_ylabel("Prima / size", color=fg)
    ax.set_title(f"{tk}  dark pool", color=fg, loc="left")
    fig.tight_layout()
    return fig

PARES = [("QQQ", "NDX"), ("SPY", "SPX"), ("IWM", "RUT")]
