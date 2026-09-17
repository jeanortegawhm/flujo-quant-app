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
        params["min_strike"] = round(float(spot) * 0.985, 2)
        params["max_strike"] = round(float(spot) * 1.015, 2)
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
        df = df[abs(df["strike"] - float(spot)) / max(abs(float(spot)), 1) <= 0.02]
    return df

def darkpool(tk, limit=80):
    data = get(f"https://api.unusualwhales.com/api/darkpool/{tk}", {"limit": limit})
    if not isinstance(data, list):
        data = get("https://api.unusualwhales.com/api/darkpool/recent", {"limit": limit, "ticker_symbol": tk})
    if not isinstance(data, list):
        return pd.DataFrame()
    df = pd.DataFrame(data)
    for c in ("price", "size", "premium", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["price"])

def _tema(ax, bg="#0b1220", fg="#e8eef7"):
    ax.set_facecolor(bg)
    ax.tick_params(colors=fg, labelsize=8)
    ax.grid(True, color="#1d2a3d", alpha=0.4)
    for s in ax.spines.values():
        s.set_color("#1d2a3d")

def _f(x):
    try:
        return float(x)
    except Exception:
        return None

def fig_gex(tk, df, niv, spot=None):
    bg, fg = "#0b1220", "#e8eef7"
    fig, ax = plt.subplots(figsize=(9.2, 5.6), facecolor=bg)
    _tema(ax)
    if df is None or df.empty:
        ax.text(0.5, 0.5, f"Sin perfil GEX · {tk}", ha="center", va="center",
                transform=ax.transAxes, color="#8b9bb0")
        ax.set_title(tk, color=fg, loc="left")
        fig.tight_layout()
        return fig

    lo, hi = float(df["strike"].min()), float(df["strike"].max())
    if spot:
        lo = min(lo, float(spot))
        hi = max(hi, float(spot))
    pad = max((hi - lo) * 0.12, 0.4)
    ax.set_ylim(lo - pad, hi + pad)

    alto = max((hi - lo) / max(len(df), 8), 0.15)
    ax.barh(df["strike"], df["call_gex"], height=alto * 0.7, color="#2ecc71", alpha=0.9, label="Call GEX")
    ax.barh(df["strike"], df["put_gex"], height=alto * 0.7, color="#9b59b6", alpha=0.9, label="Put GEX")
    ax.axvline(0, color=fg, lw=0.6, alpha=0.4)

    if spot:
        ax.axhline(float(spot), color="#6ea8ff", lw=1.4, ls="--", label=f"Spot {float(spot):.2f}")

    etiquetas = {
        "call_wall": ("CW", "#2ecc71"),
        "put_wall": ("PW", "#e74c3c"),
        "gamma_flip": ("QF", "#1aa3a3"),
        "gamma_magnet": ("MAG", "#f1c40f"),
    }
    for k, (lab, col) in etiquetas.items():
        v = _f(niv.get(k))
        if v is None or v < lo - pad or v > hi + pad:
            continue
        ax.axhline(v, color=col, lw=1.15, ls=":")
        ax.text(0.99, v, f" {lab} {v:.2f}", transform=ax.get_yaxis_transform(),
                color=col, va="center", ha="right", fontsize=8, fontweight="bold")

    ax.set_xlabel("GEX", color=fg)
    ax.set_ylabel("Strike", color=fg)
    ax.set_title(f"{tk}  perfil GEX  (zoom ±2%)", color=fg, loc="left")
    ax.legend(facecolor=bg, labelcolor=fg, fontsize=8, loc="lower right")
    fig.tight_layout()
    return fig

def fig_oi(tk, o):
    bg, fg = "#0b1220", "#e8eef7"
    fig, axs = plt.subplots(1, 2, figsize=(9.2, 3.3), facecolor=bg)
    for ax in axs:
        _tema(ax)
    def n(x):
        try:
            return float(x)
        except Exception:
            return 0.0
    axs[0].bar(["Call OI", "Put OI"], [n(o.get("call_open_interest")), n(o.get("put_open_interest"))],
               color=["#2ecc71", "#9b59b6"])
    axs[0].set_title("Open interest", color=fg, loc="left")
    axs[1].bar(["Call vol", "Put vol"], [n(o.get("call_volume")), n(o.get("put_volume"))],
               color=["#2ecc71", "#9b59b6"])
    axs[1].set_title("Volumen opciones", color=fg, loc="left")
    fig.suptitle(tk, color=fg, x=0.02, ha="left")
    fig.tight_layout()
    return fig

def fig_dp(tk, dp):
    """Perfil de dark pool por precio (estilo UW), no scatter aplastado."""
    bg, fg = "#0b1220", "#e8eef7"
    fig, ax = plt.subplots(figsize=(9.2, 4.2), facecolor=bg)
    _tema(ax)
    if dp is None or dp.empty or "price" not in dp.columns:
        ax.text(0.5, 0.5, f"Sin dark pool · {tk}", ha="center", va="center",
                transform=ax.transAxes, color="#8b9bb0")
        fig.tight_layout()
        return fig

    val = dp["premium"] if "premium" in dp.columns else dp.get("size", pd.Series(1, index=dp.index))
    val = pd.to_numeric(val, errors="coerce").fillna(0)
    # recorta el print extremo para que no aplasté el resto
    tope = val.quantile(0.92) if len(val) > 5 else val.max()
    val = val.clip(upper=max(tope, 1))
    px = pd.to_numeric(dp["price"], errors="coerce")
    paso = max((px.max() - px.min()) / 28, 0.02)
    bins = np.arange(px.min() - paso, px.max() + 2 * paso, paso)
    s = pd.DataFrame({"px": px, "v": val}).dropna()
    s["bin"] = pd.cut(s["px"], bins=bins, labels=bins[:-1]).astype(float)
    prof = s.groupby("bin")["v"].sum()

    ax.barh(prof.index, prof.values, height=paso * 0.85, color="#3d7eff", alpha=0.88)
    last = float(px.iloc[0])
    ax.axhline(last, color="#f1c40f", lw=1.2, ls="--", label=f"último {last:.2f}")
    ax.set_xlabel("Prima agregada (recortada P92)", color=fg)
    ax.set_ylabel("Precio", color=fg)
    ax.set_title(f"{tk}  dark pool profile", color=fg, loc="left")
    ax.legend(facecolor=bg, labelcolor=fg, fontsize=8)
    fig.tight_layout()
    return fig

PARES = [("QQQ", "NDX"), ("SPY", "SPX"), ("IWM", "RUT")]
