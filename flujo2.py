import os
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
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
GEX_TICKER = {k: k for k in GRUPOS}
MIN_BURBUJA = {
    "SPX": 200_000_000, "SPY": 150_000_000, "QQQ": 150_000_000,
    "IWM": 40_000_000, "IBIT": 8_000_000, "GLD": 80_000_000,
}
MAX_ETIQUETAS = 6
MIN_PREMIUM = 150000
LIMIT = 200
INTERVALO_MINUTOS = 5
TZ_MERCADO = ZoneInfo("America/New_York")
TZ_VER = ZoneInfo("America/Bogota")
CARPETA = os.getenv("FLUJOS_DIR", os.path.join(os.path.expanduser("~"), "flujos"))
os.makedirs(CARPETA, exist_ok=True)
headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
BLOQUES = [(9, 25, 11, 30), (11, 30, 13, 30), (13, 30, 15, 0), (15, 0, 16, 15)]
LOG_SENALES = os.path.join(CARPETA, "senales.csv")

def mercado_abierto():
    ahora = datetime.now(TZ_MERCADO)
    if ahora.weekday() >= 5:
        return False
    return ahora.replace(hour=9, minute=30) <= ahora <= ahora.replace(hour=16, minute=0)

def dia_habil_anterior():
    d = datetime.now(TZ_MERCADO).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def etiquetas_hora(x, pos):
    dt = mdates.num2date(x, tz=TZ_MERCADO)
    col = dt.astimezone(TZ_VER)
    return f"{dt.strftime('%H:%M')} NY\n{col.strftime('%H:%M')} COL"

def fmt_usd(x):
    x = float(x or 0)
    s = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1_000_000_000:
        return f"{s}${x/1e9:.2f}B"
    if x >= 1_000_000:
        return f"{s}${x/1e6:.1f}M"
    return f"{s}${x:,.0f}"

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

def cerca(valor, spot, max_pct=0.025):
    if valor is None or not spot:
        return False
    return abs(valor - spot) / abs(spot) <= max_pct

def get_json(url, params=None):
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=25)
        if r.status_code != 200 or not r.text:
            print(f"  {url.split('/')[-1]} {r.status_code}")
            return None
        payload = r.json()
        return payload.get("data") if isinstance(payload, dict) else payload
    except Exception as e:
        print("  api fallo:", e)
        return None

def filtrar_niveles(niveles, spot):
    limpios = {}
    for k, v in (niveles or {}).items():
        if cerca(v, spot, 0.025):
            limpios[k] = v
        else:
            print(f"  descarto {k}={v} (lejos de spot {spot})")
    return limpios

def obtener_niveles(grupo, fecha, spot=None):
    tk = GEX_TICKER.get(grupo, grupo)
    niveles = {}
    data = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
                    {"date": str(fecha), "source": "oi"})
    if not isinstance(data, dict):
        data = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
                        {"date": str(fecha), "source": "vol"})
    if isinstance(data, dict):
        niveles = {k: num(data.get(v)) for k, v in
                   (("PW", "put_wall"), ("QF", "gamma_flip"), ("CW", "call_wall"))}
        niveles = {k: v for k, v in niveles.items() if v is not None}
    rows = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/spot-exposures/strike",
                    {"date": str(fecha), "limit": 500})
    extra = {}
    if isinstance(rows, list) and rows and spot:
        gex = []
        for row in rows:
            k = num(row.get("strike"))
            cg = num(row.get("call_gamma_oi") or row.get("call_gamma_vol") or 0) or 0
            pg = num(row.get("put_gamma_oi") or row.get("put_gamma_vol") or 0) or 0
            if k is not None:
                gex.append((k, cg + pg, cg, pg))
        if gex:
            gex.sort()
            below = [x for x in gex if x[0] <= spot]
            above = [x for x in gex if x[0] >= spot]
            if below:
                extra["PW"] = max(below, key=lambda x: abs(x[3]) if x[3] else abs(x[1]))[0]
            if above:
                extra["CW"] = max(above, key=lambda x: x[2] if x[2] else abs(x[1]))[0]
            cruce = None
            for a, b in zip(gex, gex[1:]):
                if a[1] * b[1] < 0:
                    t = abs(a[1]) / (abs(a[1]) + abs(b[1]) + 1e-9)
                    cand = a[0] + t * (b[0] - a[0])
                    if cruce is None or abs(cand - spot) < abs(cruce - spot):
                        cruce = cand
            if cruce:
                extra["QF"] = cruce
    extra = filtrar_niveles(extra, spot)
    niveles.update(extra)
    niveles = filtrar_niveles(niveles, spot)
    print(f"  Niveles {grupo}: {niveles}")
    return niveles

def obtener_vol(grupo, fecha):
    tk = GEX_TICKER.get(grupo, grupo)
    out = {"iv": None, "ivr": None, "rv": None, "move1d": None}
    d = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/volatility/stats",
                 {"date": str(fecha)})
    if isinstance(d, dict):
        out["iv"] = pct(d.get("iv"))
        out["ivr"] = pct(d.get("iv_rank"))
        out["rv"] = pct(d.get("rv"))
    rows = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/interpolated-iv",
                    {"date": str(fecha)})
    if isinstance(rows, list):
        for row in rows:
            if int(row.get("days", 0) or 0) == 1:
                out["move1d"] = pct(row.get("implied_move_perc"))
                break
    print(f"  IV {grupo}: {out}")
    return out

def obtener_net_ticks(grupo, fecha):
    frames = []
    for tk in GRUPOS[grupo]:
        rows = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/net-prem-ticks",
                        {"date": str(fecha)})
        if not isinstance(rows, list) or not rows:
            continue
        df = pd.DataFrame(rows)
        if "tape_time" not in df.columns:
            continue
        df["hora"] = pd.to_datetime(df["tape_time"], utc=True, errors="coerce").dt.tz_convert(TZ_MERCADO)
        df["net_call"] = pd.to_numeric(df.get("net_call_premium", 0), errors="coerce").fillna(0)
        df["net_put"] = pd.to_numeric(df.get("net_put_premium", 0), errors="coerce").fillna(0)
        df["agres"] = df["net_call"] - df["net_put"]
        frames.append(df[["hora", "agres"]])
    if not frames:
        return pd.Series(dtype=float)
    g = pd.concat(frames).dropna()
    g["min1"] = g["hora"].dt.tz_convert(TZ_VER).dt.floor("1min")
    return g.groupby("min1")["agres"].sum()

def lado_trade(row):
    tags = str(row.get("tags", "")).lower()
    if "ask_side" in tags:
        return "COMPRA"
    if "bid_side" in tags:
        return "VENTA"
    price = pd.to_numeric(row.get("price"), errors="coerce")
    bid = pd.to_numeric(row.get("nbbo_bid"), errors="coerce")
    ask = pd.to_numeric(row.get("nbbo_ask"), errors="coerce")
    if pd.notna(price) and pd.notna(bid) and pd.notna(ask):
        return "COMPRA" if price >= (bid + ask) / 2 else "VENTA"
    return "INDEF"

def procesar_df(data, ticker):
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["origen"] = ticker
    raw = df["executed_at"] if "executed_at" in df.columns else df.get("created_at")
    if raw is not None and len(raw) and str(raw.iloc[0]).replace(".", "", 1).isdigit():
        df["hora"] = pd.to_datetime(pd.to_numeric(raw, errors="coerce"), unit="ms", utc=True, errors="coerce")
    else:
        df["hora"] = pd.to_datetime(raw, utc=True, errors="coerce")
    df["hora"] = df["hora"].dt.tz_convert(TZ_MERCADO)
    df["premium"] = pd.to_numeric(df.get("premium", 0), errors="coerce").fillna(0)
    df["delta"] = pd.to_numeric(df.get("delta", 0), errors="coerce").fillna(0)
    df["size"] = pd.to_numeric(df.get("size", 0), errors="coerce").fillna(0)
    df["spot"] = pd.to_numeric(df.get("underlying_price", 0), errors="coerce").fillna(0)
    if "option_type" not in df.columns:
        df["option_type"] = df.get("option_symbol", "").astype(str).apply(
            lambda x: "call" if "C" in str(x)[-10:] else "put")
    df["option_type"] = df["option_type"].astype(str).str.lower()
    df["lado"] = df.apply(lado_trade, axis=1)
    df["contrato"] = df["option_type"].map(lambda x: "CALL" if "call" in x else "PUT")
    signo = []
    for _, row in df.iterrows():
        if row["lado"] == "COMPRA" and row["contrato"] == "CALL":
            signo.append(1)
        elif row["lado"] == "COMPRA" and row["contrato"] == "PUT":
            signo.append(-1)
        elif row["lado"] == "VENTA" and row["contrato"] == "CALL":
            signo.append(-1)
        elif row["lado"] == "VENTA" and row["contrato"] == "PUT":
            signo.append(1)
        else:
            signo.append(0)
    df["signo"] = signo
    df["qdelta"] = df["signo"] * df["delta"].abs() * df["size"] * 100
    df["exposicion"] = df["delta"].abs() * df["size"] * 100 * df["spot"]
    df.loc[df["spot"] <= 0, "exposicion"] = 0
    return df.dropna(subset=["hora"])

def obtener_tape(fecha, ticker):
    partes, url = [], "https://api.unusualwhales.com/api/option-trades"
    for h1, m1, h2, m2 in BLOQUES:
        inicio = datetime(fecha.year, fecha.month, fecha.day, h1, m1, tzinfo=TZ_MERCADO).astimezone(ZoneInfo("UTC"))
        fin = datetime(fecha.year, fecha.month, fecha.day, h2, m2, tzinfo=TZ_MERCADO).astimezone(ZoneInfo("UTC"))
        params = {
            "ticker_symbol": ticker,
            "newer_than": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "older_than": fin.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "min_premium": MIN_PREMIUM,
            "limit": LIMIT,
        }
        data = get_json(url, params)
        data = data if isinstance(data, list) else []
        print(f"  {ticker} {h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}: {len(data)}")
        partes.extend(data)
        time.sleep(0.15)
    if not partes:
        return pd.DataFrame()
    return procesar_df(partes, ticker).drop_duplicates(subset=["hora", "premium", "size"]).sort_values("hora")

def cargar_precio(grupo, fecha):
    px = yf.download(YAHOO.get(grupo, grupo), period="7d", interval="1m",
                     progress=False, auto_adjust=True)
    if px.empty:
        px = yf.download(YAHOO.get(grupo, grupo), period="7d", interval="5m",
                         progress=False, auto_adjust=True)
    if px.empty:
        return px
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    px = px.copy()
    px.index = pd.to_datetime(px.index)
    px.index = px.index.tz_localize("America/New_York") if px.index.tz is None else px.index.tz_convert(TZ_MERCADO)
    return px[px.index.date == fecha].between_time("09:30", "16:00")

def ventana(serie, minutos=30):
    if serie is None or len(serie) == 0:
        return 0.0
    corte = serie.index.max() - pd.Timedelta(minutes=minutos)
    return float(serie[serie.index >= corte].sum())

def leer_senales(last, px_c, niveles, qdelta, expo, grandes):
    qf, pw, cw = niveles.get("QF"), niveles.get("PW"), niveles.get("CW")
    neto = float(qdelta.sum()) if len(qdelta) else 0.0
    q30, q10 = ventana(qdelta, 30), ventana(qdelta, 10)
    e30 = ventana(expo, 30)
    lo = float(px_c["Close"].min())
    last_n = px_c["Close"].tail(10)
    minimos_nuevos = len(last_n) >= 5 and last <= last_n.min() * 1.001
    senales = []
    if qf and last < qf and q30 < 0 and e30 > 0:
        senales.append("FLUSH")
    if pw and last <= pw * 1.004:
        senales.append("PISO_PW")
    if cw and last >= cw:
        senales.append("SOBRE_CW")
    if neto < 0 and q30 > 0 and not minimos_nuevos:
        senales.append("CAMBIO")
    if qf and last >= qf and q30 >= 0:
        senales.append("RECLAIM_QF")
    elif qf and last < qf and last > lo:
        senales.append("FALSO_REBOTE")
    if not grandes.empty:
        ult = grandes.sort_values("hora").iloc[-1]
        if ult["signo"] < 0 and last > float(px_c["Close"].quantile(0.35)):
            senales.append("HEDGE")
    if q10 > 0 and q30 > 0:
        senales.append("FLUJO_COMPRA")
    elif q10 < 0 and q30 < 0:
        senales.append("FLUJO_VENTA")
    if not senales:
        senales.append("NEUTRO")
    if "RECLAIM_QF" in senales:
        sesgo = "CAMBIO ALCISTA"
    elif "CAMBIO" in senales and "FALSO_REBOTE" not in senales:
        sesgo = "CAMBIO TEMPRANO"
    elif "FLUSH" in senales and "PISO_PW" in senales:
        sesgo = "BAJISTA EN PISO"
    elif "FLUSH" in senales or "FLUJO_VENTA" in senales:
        sesgo = "BAJISTA"
    elif "FLUJO_COMPRA" in senales:
        sesgo = "ALCISTA"
    else:
        sesgo = "REPARACION"
    return sesgo, senales, neto, q30

def guardar_senal(fecha, grupo, sesgo, senales, neto, q30, last, niveles):
    nuevo = not os.path.exists(LOG_SENALES)
    with open(LOG_SENALES, "a", encoding="utf-8") as f:
        if nuevo:
            f.write("ts,fecha,grupo,sesgo,senales,qdelta,q30,precio,qf,pw,cw\n")
        f.write(
            f"{datetime.now(TZ_VER)},{fecha},{grupo},{sesgo},{'|'.join(senales)},"
            f"{neto},{q30},{last},{niveles.get('QF','')},{niveles.get('PW','')},{niveles.get('CW','')}\n"
        )

def pintar_nivel(ax, y, nombre, color):
    ax.axhline(y, color=color, ls="--", lw=1.15, alpha=0.95, zorder=4)
    ax.text(0.004, y, f"{nombre} {y:.2f}", transform=ax.get_yaxis_transform(),
            va="bottom", ha="left", fontsize=8, color="white", fontweight="bold",
            bbox=dict(fc=color, ec="none", pad=0.25))

def escala_auto(serie):
    mx = float(serie.abs().max()) if len(serie) else 0
    if mx >= 1e8:
        return 1e9, "$B"
    if mx >= 1e5:
        return 1e6, "$M"
    return 1, "$"

def grafico(grupo, df, etiqueta, fecha, niveles, vol, ticks):
    px = cargar_precio(grupo, fecha)
    if px.empty:
        print("  sin precio", grupo, fecha)
        return None
    bg, fg, grid, linea = "#0b1220", "#e8eef7", "#1d2a3d", "#7eb6ff"
    umbral = MIN_BURBUJA.get(grupo, 40_000_000)
    px_c = px.copy()
    px_c.index = px_c.index.tz_convert(TZ_VER)
    last = float(px_c["Close"].iloc[-1])
    last_t = px_c.index[-1]
    g = df.copy() if df is not None else pd.DataFrame()
    if not g.empty and g["spot"].max() <= 0:
        tmp = []
        for _, row in g.iterrows():
            i = px.index.get_indexer([row["hora"]], method="nearest")[0]
            tmp.append(abs(row["delta"]) * row["size"] * 100 * float(px["Close"].iloc[i]))
        g["exposicion"] = tmp
    if not g.empty:
        g["min1"] = g["hora"].dt.tz_convert(TZ_VER).dt.floor("1min")
        expo = g.groupby("min1")["exposicion"].sum()
        qdelta = g.groupby("min1")["qdelta"].sum()
        agres_fb = g.groupby("min1").apply(lambda x: (x["signo"] * x["exposicion"]).sum())
        grandes = g[g["exposicion"] >= umbral].sort_values("exposicion", ascending=False)
        if grandes.empty:
            grandes = g.nlargest(4, "exposicion")
        top = grandes.head(MAX_ETIQUETAS)
        max_exp = g["exposicion"].max()
    else:
        expo = qdelta = agres_fb = pd.Series(dtype=float)
        grandes = top = g
        max_exp = 0
    agres = ticks if ticks is not None and len(ticks) else agres_fb
    sesgo, senales, neto, q30 = leer_senales(last, px_c, niveles, qdelta, expo, grandes if not grandes.empty else pd.DataFrame())
    color_s = "#2ecc71" if "ALCISTA" in sesgo else "#e74c3c" if "BAJISTA" in sesgo else "#f1c40f"
    iv_txt = []
    if vol.get("iv") is not None:
        iv_txt.append(f"IV {vol['iv']:.1f}%")
    if vol.get("ivr") is not None:
        iv_txt.append(f"IVR {vol['ivr']:.0f}")
    if vol.get("move1d") is not None:
        iv_txt.append(f"EM1D {vol['move1d']:.2f}%")
    caja = (
        f"{sesgo}   max {fmt_usd(max_exp)}\n"
        f"{last_t.strftime('%H:%M')} COL · {grupo} {last:,.2f}\n"
        f"señales: {', '.join(senales)}\n"
        f"qdelta día {fmt_usd(neto)} | 30m {fmt_usd(q30)}"
    )
    if iv_txt:
        caja += "\n" + "  ".join(iv_txt)
    caja += "\nprint grande != direccion"
    print(" ", caja.replace("\n", " | "))
    guardar_senal(fecha, grupo, sesgo, senales, neto, q30, last, niveles)

    fig, axs = plt.subplots(4, 1, figsize=(14, 13), sharex=True,
                            gridspec_kw={"height_ratios": [3.3, 1.05, 1.15, 0.95]})
    fig.patch.set_facecolor(bg)
    ax1, axA, axT, axD = axs
    for ax in axs:
        ax.set_facecolor(bg)
        ax.grid(True, color=grid)
        ax.tick_params(colors=fg)
        for s in ax.spines.values():
            s.set_color(grid)
    ax1.plot(px_c.index, px_c["Close"], color=linea, lw=1.5)
    lo, hi = float(px_c["Close"].min()), float(px_c["Close"].max())
    pad = (hi - lo) * 0.10 or 1
    ax1.set_ylim(lo - pad, hi + pad)
    ax1.set_title(f"PRECIO DEL {grupo}   |   {etiqueta} {fecha}", loc="left", color=fg, fontsize=11)
    ax1.text(0.01, 0.03, caja, transform=ax1.transAxes, color=color_s, fontsize=8,
             fontweight="bold", va="bottom",
             bbox=dict(boxstyle="round,pad=0.35", fc=bg, ec=color_s, alpha=0.92))
    ax1.text(1.01, last, f"{last:.2f}", transform=ax1.get_yaxis_transform(),
             va="center", fontsize=8, color="white", bbox=dict(fc=linea, ec="none", pad=0.25))
    if "CW" in niveles:
        pintar_nivel(ax1, niveles["CW"], "CW", "#6c7ae0")
    if "QF" in niveles:
        pintar_nivel(ax1, niveles["QF"], "QF", "#1aa3a3")
    if "PW" in niveles:
        pintar_nivel(ax1, niveles["PW"], "PW", "#d24b6b")
    ids_top = set(top.index) if not top.empty else set()
    for idx, row in (grandes.iterrows() if not grandes.empty else []):
        i = px.index.get_indexer([row["hora"]], method="nearest")[0]
        x, y = px_c.index[i], float(px["Close"].iloc[i])
        put = row["contrato"] == "PUT"
        ax1.scatter(x, y, s=420, facecolors="none", edgecolors="#f1c40f", lw=2.0, zorder=8)
        ax1.scatter(x, y, s=42, c="#f1c40f", zorder=9)
        ax1.scatter(x, y, s=50, marker=("v" if put else "^"),
                    c=("#e74c3c" if put else "#2ecc71"), zorder=10)
        if idx in ids_top:
            ax1.annotate(fmt_usd(row["exposicion"]), xy=(x, y), xytext=(0, 11),
                         textcoords="offset points", ha="center", color="#f1c40f",
                         fontsize=8, fontweight="bold")
    if len(agres):
        esc, uni = escala_auto(agres)
        axA.bar(agres.index, agres.values / esc, width=0.0007,
                color=["#2ecc71" if v >= 0 else "#e74c3c" for v in agres.values])
        axA.set_ylabel(f"AGRESOR {uni}", fontsize=8, color=fg)
    else:
        axA.set_ylabel("AGRESOR", fontsize=8, color=fg)
    axA.axhline(0, color="#888", lw=0.6)
    if len(expo):
        axT.bar(expo.index, expo.values / 1e6, width=0.0007,
                color=["#f1c40f" if v >= umbral else "#3d4f66" for v in expo.values])
        for t, v in expo.nlargest(MAX_ETIQUETAS).items():
            if v >= umbral:
                axT.text(t, v / 1e6, fmt_usd(v), ha="center", va="bottom",
                         color="#f1c40f", fontsize=7, fontweight="bold")
    axT.set_ylabel("TOTAL $M", fontsize=8, color=fg)
    if len(qdelta):
        esc, uni = escala_auto(qdelta)
        axD.bar(qdelta.index, qdelta.values / esc, width=0.0007,
                color=["#2ecc71" if v >= 0 else "#e74c3c" for v in qdelta.values])
        axD.set_ylabel(f"QDELTA {uni}", fontsize=8, color=fg)
    else:
        axD.set_ylabel("QDELTA", fontsize=8, color=fg)
    axD.axhline(0, color="#888", lw=0.6)
    axD.xaxis.set_major_formatter(FuncFormatter(etiquetas_hora))
    plt.tight_layout()
    ruta = os.path.join(CARPETA, f"{grupo}_Q_{etiqueta}_{fecha}_{datetime.now().strftime('%H%M%S')}.png")
    plt.savefig(ruta, dpi=160, facecolor=bg)
    plt.close()
    print("Grafico", ruta)
    return sesgo, senales

def procesar(fecha, etiqueta):
    frames = []
    for grupo, tickers in GRUPOS.items():
        print("Grupo", grupo, fecha)
        partes = []
        for tk in tickers:
            df = obtener_tape(fecha, tk)
            if not df.empty:
                partes.append(df)
        df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
        if not df.empty:
            frames.append(df)
        px = cargar_precio(grupo, fecha)
        spot = float(px["Close"].iloc[-1]) if not px.empty else None
        niveles = obtener_niveles(grupo, fecha, spot)
        vol = obtener_vol(grupo, fecha)
        ticks = obtener_net_ticks(grupo, fecha)
        grafico(grupo, df, etiqueta, fecha, niveles, vol, ticks)
    if frames:
        csv = os.path.join(CARPETA, f"tape_{etiqueta}_{fecha}_{datetime.now().strftime('%H%M%S')}.csv")
        pd.concat(frames, ignore_index=True).to_csv(csv, index=False)
        print("CSV", csv)

def main():
    if not API_KEY:
        print("Falta UW_API_KEY")
        return
    print("=" * 70)
    print("QUANTIUM + SEÑALES | GEX | IV | TAPE")
    print("Salida:", CARPETA)
    print("=" * 70)
    modo = os.getenv("MODO_FLUJO", "AMBOS")
    ayer = dia_habil_anterior()
    hoy = datetime.now(TZ_MERCADO).date()
    print("MODO:", modo)
    if modo == "HOY":
        procesar(hoy, "HOY")
    elif modo == "AYER":
        procesar(ayer, "AYER")
    else:
        procesar(ayer, "AYER")
        procesar(hoy, "HOY")

if __name__ == "__main__":
    main()