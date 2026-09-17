import os
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time
import warnings
warnings.filterwarnings("ignore")

API_KEY = os.getenv("UW_API_KEY", "")

GRUPOS = {
    "SPX": ["SPX", "SPXW"],
    "SPY": ["SPY"],
    "QQQ": ["QQQ"],
    "IWM": ["IWM"],
    "IBIT": ["IBIT"],
    "GLD": ["GLD"],
}
YAHOO = {
    "SPX": "^GSPC", "SPY": "SPY", "QQQ": "QQQ",
    "IWM": "IWM", "IBIT": "IBIT", "GLD": "GLD",
}
MIN_BURBUJA = {
    "SPX": 250_000_000, "SPY": 200_000_000, "QQQ": 200_000_000,
    "IWM": 50_000_000, "IBIT": 15_000_000, "GLD": 100_000_000,
}
DIAS = 15
MIN_PREMIUM = 250000
LIMIT = 150
MAX_ETIQUETAS = 8
TZ = ZoneInfo("America/New_York")
TZ_COL = ZoneInfo("America/Bogota")
CARPETA = os.getenv("FLUJOS_DIR", os.path.join(os.path.expanduser("~"), "flujos"))
os.makedirs(CARPETA, exist_ok=True)
headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}

def num(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None

def pct(x):
    v = num(x)
    if v is None:
        return None
    return v * 100 if abs(v) <= 3 else v

def fmt_usd(x):
    x = float(x or 0)
    s = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1e9:
        return f"{s}${x/1e9:.2f}B"
    if x >= 1e6:
        return f"{s}${x/1e6:.1f}M"
    return f"{s}${x:,.0f}"

def get_json(url, params=None):
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=25)
        if r.status_code != 200 or not r.text:
            return None
        payload = r.json()
        return payload.get("data") if isinstance(payload, dict) else payload
    except Exception as e:
        print("  api:", e)
        return None

def habiles(n=DIAS):
    d, out = datetime.now(TZ).date(), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))

def cerca(v, spot, p=0.04):
    return v is not None and spot and abs(v - spot) / abs(spot) <= p

def obtener_niveles(tk, fecha, spot):
    data = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
                    {"date": str(fecha), "source": "oi"})
    if not isinstance(data, dict):
        data = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
                        {"date": str(fecha), "source": "vol"})
    niv = {}
    if isinstance(data, dict):
        for k, campo in (("PW", "put_wall"), ("QF", "gamma_flip"), ("CW", "call_wall")):
            v = num(data.get(campo))
            if cerca(v, spot, 0.04):
                niv[k] = v
    print(f"  GEX-OI {tk}: {niv}")
    return niv

def obtener_vol(tk, fecha):
    out = {"iv": None, "ivr": None, "move1d": None}
    d = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/volatility/stats",
                 {"date": str(fecha)})
    if isinstance(d, dict):
        out["iv"] = pct(d.get("iv"))
        out["ivr"] = pct(d.get("iv_rank"))
    rows = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/interpolated-iv",
                    {"date": str(fecha)})
    if isinstance(rows, list):
        for row in rows:
            if int(row.get("days", 0) or 0) in (7, 5, 6):
                out["move1d"] = pct(row.get("implied_move_perc"))
                break
    return out

def lado(row):
    tags = str(row.get("tags", "")).lower()
    if "ask_side" in tags:
        return "COMPRA"
    if "bid_side" in tags:
        return "VENTA"
    p = pd.to_numeric(row.get("price"), errors="coerce")
    b = pd.to_numeric(row.get("nbbo_bid"), errors="coerce")
    a = pd.to_numeric(row.get("nbbo_ask"), errors="coerce")
    if pd.notna(p) and pd.notna(b) and pd.notna(a):
        return "COMPRA" if p >= (b + a) / 2 else "VENTA"
    return "INDEF"

def procesar_trades(data, ticker):
    df = pd.DataFrame(data)
    if df.empty:
        return df
    raw = df["executed_at"] if "executed_at" in df.columns else df.get("created_at")
    if raw is not None and len(raw) and str(raw.iloc[0]).replace(".", "", 1).isdigit():
        df["hora"] = pd.to_datetime(pd.to_numeric(raw, errors="coerce"), unit="ms", utc=True, errors="coerce")
    else:
        df["hora"] = pd.to_datetime(raw, utc=True, errors="coerce")
    df["hora"] = df["hora"].dt.tz_convert(TZ)
    df["premium"] = pd.to_numeric(df.get("premium", 0), errors="coerce").fillna(0)
    df["delta"] = pd.to_numeric(df.get("delta", 0), errors="coerce").fillna(0)
    df["size"] = pd.to_numeric(df.get("size", 0), errors="coerce").fillna(0)
    df["spot"] = pd.to_numeric(df.get("underlying_price", 0), errors="coerce").fillna(0)
    df["option_type"] = df.get("option_type", "put").astype(str).str.lower()
    df["lado"] = df.apply(lado, axis=1)
    df["contrato"] = np.where(df["option_type"].str.contains("call"), "CALL", "PUT")
    signo = []
    for _, r in df.iterrows():
        if r["lado"] == "COMPRA" and r["contrato"] == "CALL":
            signo.append(1)
        elif r["lado"] == "COMPRA" and r["contrato"] == "PUT":
            signo.append(-1)
        elif r["lado"] == "VENTA" and r["contrato"] == "CALL":
            signo.append(-1)
        elif r["lado"] == "VENTA" and r["contrato"] == "PUT":
            signo.append(1)
        else:
            signo.append(0)
    df["signo"] = signo
    df["qdelta"] = df["signo"] * df["delta"].abs() * df["size"] * 100
    df["exposicion"] = df["delta"].abs() * df["size"] * 100 * df["spot"].clip(lower=0)
    df["origen"] = ticker
    df["dia"] = df["hora"].dt.date
    return df.dropna(subset=["hora"])

def tape_dia(fecha, ticker):
    inicio = datetime(fecha.year, fecha.month, fecha.day, 9, 25, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    fin = datetime(fecha.year, fecha.month, fecha.day, 16, 15, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    data = get_json("https://api.unusualwhales.com/api/option-trades", {
        "ticker_symbol": ticker,
        "newer_than": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "older_than": fin.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "min_premium": MIN_PREMIUM,
        "limit": LIMIT,
    })
    data = data if isinstance(data, list) else []
    print(f"  {ticker} {fecha}: {len(data)}")
    time.sleep(0.12)
    return procesar_trades(data, ticker) if data else pd.DataFrame()

def precio_diario(grupo):
    px = yf.download(YAHOO[grupo], period="2mo", interval="1d", progress=False, auto_adjust=True)
    if px.empty:
        return px
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    px.index = pd.to_datetime(px.index)
    return px.tail(DIAS + 2)

def senal_swing(px, last, niveles, q5, q15, vol):
    qf, pw, cw = niveles.get("QF"), niveles.get("PW"), niveles.get("CW")
    cierres = px["Close"].tail(5)
    sobre_qf = qf is not None and (cierres > qf).sum() >= 3
    bajo_qf = qf is not None and (cierres < qf).sum() >= 3
    senales = []
    if sobre_qf and q5 >= 0:
        senales.append("SWING_ALCISTA")
    if bajo_qf and q5 <= 0:
        senales.append("SWING_BAJISTA")
    if qf and last >= qf and q5 > 0:
        senales.append("RECLAIM_QF_SWING")
    if qf and last < qf and last > float(px["Close"].min()):
        senales.append("DEBIL_BAJO_QF")
    if pw and last <= pw * 1.01:
        senales.append("PISO_PW_SWING")
    if cw and last >= cw * 0.995:
        senales.append("TECHO_CW")
    if q15 < 0 and q5 > 0:
        senales.append("GIRO_5D")
    if q15 > 0 and q5 < 0:
        senales.append("PERDIDA_5D")
    ivr = vol.get("ivr")
    if ivr is not None and ivr >= 70:
        senales.append("IVR_ALTA_vender_premio")
    elif ivr is not None and ivr <= 25:
        senales.append("IVR_BAJA_comprar_premio")
    if not senales:
        senales.append("RANGO")
    if "SWING_ALCISTA" in senales or "RECLAIM_QF_SWING" in senales:
        sesgo = "SWING ALCISTA"
    elif "SWING_BAJISTA" in senales:
        sesgo = "SWING BAJISTA"
    elif "GIRO_5D" in senales:
        sesgo = "GIRO ALCISTA 5D"
    elif "PERDIDA_5D" in senales:
        sesgo = "GIRO BAJISTA 5D"
    else:
        sesgo = "SWING NEUTRO / RANGO"
    return sesgo, senales

def pintar_nivel(ax, y, nombre, color):
    ax.axhline(y, color=color, ls="--", lw=1.2)
    ax.text(0.01, y, f"{nombre} {y:.2f}", transform=ax.get_yaxis_transform(),
            color="white", fontsize=8, fontweight="bold", va="bottom",
            bbox=dict(fc=color, ec="none", pad=0.25))

def grafico_swing(grupo, px, df, niveles, vol):
    if px.empty:
        print("  sin precio", grupo)
        return
    bg, fg, grid = "#0b1220", "#e8eef7", "#1d2a3d"
    last = float(px["Close"].iloc[-1])
    umbral = MIN_BURBUJA[grupo]
    if df is None or df.empty:
        q5 = q15 = 0
        grandes = pd.DataFrame()
        diario = pd.Series(dtype=float)
    else:
        df = df.copy()
        df["dia"] = pd.to_datetime(df["dia"])
        diario = df.groupby(df["dia"].dt.date)["qdelta"].sum()
        ult = sorted(diario.index)[-5:] if len(diario) else []
        q5 = float(diario.loc[diario.index.isin(ult)].sum()) if ult else 0
        q15 = float(diario.sum())
        grandes = df[df["exposicion"] >= umbral].sort_values("exposicion", ascending=False)
        if grandes.empty:
            grandes = df.nlargest(6, "exposicion")
    sesgo, senales = senal_swing(px, last, niveles, q5, q15, vol)
    color = "#2ecc71" if "ALCISTA" in sesgo else "#e74c3c" if "BAJISTA" in sesgo else "#f1c40f"
    iv = []
    if vol.get("iv") is not None:
        iv.append(f"IV {vol['iv']:.1f}%")
    if vol.get("ivr") is not None:
        iv.append(f"IVR {vol['ivr']:.0f}")
    caja = (
        f"{sesgo}\n"
        f"{', '.join(senales)}\n"
        f"qdelta 5d {fmt_usd(q5)} | {DIAS}d {fmt_usd(q15)}\n"
        f"{grupo} {last:,.2f}   " + "  ".join(iv) +
        "\nPW/QF de OI = niveles swing | print != direccion"
    )
    print(" ", caja.replace("\n", " | "))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), sharex=True,
                                   gridspec_kw={"height_ratios": [3.2, 1.1]})
    fig.patch.set_facecolor(bg)
    for ax in (ax1, ax2):
        ax.set_facecolor(bg)
        ax.grid(True, color=grid)
        ax.tick_params(colors=fg)
        for s in ax.spines.values():
            s.set_color(grid)

    x = px.index
    up = px["Close"] >= px["Open"]
    ax1.bar(x[up], px.loc[up, "High"] - px.loc[up, "Low"], bottom=px.loc[up, "Low"],
            width=0.6, color="#2ecc71", alpha=0.35)
    ax1.bar(x[~up], px.loc[~up, "High"] - px.loc[~up, "Low"], bottom=px.loc[~up, "Low"],
            width=0.6, color="#e74c3c", alpha=0.35)
    ax1.plot(x, px["Close"], color="#7eb6ff", lw=1.6)
    lo, hi = float(px["Low"].min()), float(px["High"].max())
    pad = (hi - lo) * 0.08 or 1
    ax1.set_ylim(lo - pad, hi + pad)
    ax1.set_title(f"SWING {grupo}  |  {DIAS} sesiones  |  {datetime.now(TZ_COL):%Y-%m-%d %H:%M} COL",
                  loc="left", color=fg)
    ax1.text(0.01, 0.03, caja, transform=ax1.transAxes, color=color, fontsize=8,
             fontweight="bold", va="bottom",
             bbox=dict(boxstyle="round,pad=0.4", fc=bg, ec=color, alpha=0.93))
    if "CW" in niveles:
        pintar_nivel(ax1, niveles["CW"], "CW", "#6c7ae0")
    if "QF" in niveles:
        pintar_nivel(ax1, niveles["QF"], "QF", "#1aa3a3")
    if "PW" in niveles:
        pintar_nivel(ax1, niveles["PW"], "PW", "#d24b6b")

    top = grandes.head(MAX_ETIQUETAS) if not grandes.empty else grandes
    ids = set(top.index) if not top.empty else set()
    for idx, row in (grandes.iterrows() if not grandes.empty else []):
        xd = pd.Timestamp(row["dia"])
        y = float(px["Close"].iloc[px.index.get_indexer([xd], method="nearest")[0]])
        put = row["contrato"] == "PUT"
        ax1.scatter(xd, y, s=380, facecolors="none", edgecolors="#f1c40f", lw=1.8, zorder=8)
        ax1.scatter(xd, y, s=48, marker=("v" if put else "^"),
                    c=("#e74c3c" if put else "#2ecc71"), zorder=9)
        if idx in ids:
            ax1.annotate(fmt_usd(row["exposicion"]), (xd, y), textcoords="offset points",
                         xytext=(0, 10), ha="center", color="#f1c40f", fontsize=8, fontweight="bold")

    if len(diario):
        idx = pd.to_datetime(list(diario.index))
        ax2.bar(idx, diario.values / 1e6,
                color=["#2ecc71" if v >= 0 else "#e74c3c" for v in diario.values], width=0.6)
    ax2.axhline(0, color="#888", lw=0.6)
    ax2.set_ylabel("QDELTA $M / día", color=fg, fontsize=8)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))
    plt.tight_layout()
    ruta = os.path.join(CARPETA, f"{grupo}_SWING_{datetime.now():%Y%m%d_%H%M%S}.png")
    plt.savefig(ruta, dpi=160, facecolor=bg)
    plt.close()
    print("Grafico", ruta)
    with open(os.path.join(CARPETA, "senales_swing.csv"), "a", encoding="utf-8") as f:
        if f.tell() == 0:
            f.write("ts,grupo,sesgo,senales,q5,q15,precio,qf,pw,cw\n")
        f.write(f"{datetime.now(TZ_COL)},{grupo},{sesgo},{'|'.join(senales)},{q5},{q15},{last},"
                f"{niveles.get('QF','')},{niveles.get('PW','')},{niveles.get('CW','')}\n")

def main():
    if not API_KEY:
        print("Falta UW_API_KEY")
        return
    dias = habiles(DIAS)
    hoy = dias[-1]
    print("=" * 70)
    print("MODO SWING | OI GEX | flujo", DIAS, "días")
    print("Salida:", CARPETA)
    print("=" * 70)
    for grupo, tickers in GRUPOS.items():
        print("Grupo", grupo)
        px = precio_diario(grupo)
        spot = float(px["Close"].iloc[-1]) if not px.empty else None
        partes = []
        for d in dias[-7:]:
            for tk in tickers:
                t = tape_dia(d, tk)
                if not t.empty:
                    partes.append(t)
        df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
        niveles = obtener_niveles(tickers[0], hoy, spot)
        vol = obtener_vol(tickers[0], hoy)
        grafico_swing(grupo, px, df, niveles, vol)

if __name__ == "__main__":
    main()