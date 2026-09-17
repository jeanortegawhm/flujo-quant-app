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

# ================== CONFIG ==================
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
    "SPX": 150_000_000, "SPY": 80_000_000, "QQQ": 80_000_000,
    "IWM": 25_000_000, "IBIT": 5_000_000, "GLD": 15_000_000,
}

MIN_PREMIUM = int(os.getenv("MIN_PREMIUM", "120000"))
SOLO_0DTE = os.getenv("SOLO_0DTE", "0") == "1"
UMBRAL_BURBUJA = float(os.getenv("UMBRAL_BURBUJA", "30000000"))

LIMIT = 180
MAX_ETIQUETAS = 7

TZ_MERCADO = ZoneInfo("America/New_York")
TZ_VER = ZoneInfo("America/Bogota")
CARPETA = os.getenv("FLUJOS_DIR", os.path.join(os.path.expanduser("~"), "flujos"))
os.makedirs(CARPETA, exist_ok=True)

headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
BLOQUES = [(9, 25, 11, 30), (11, 30, 13, 30), (13, 30, 15, 0), (15, 0, 16, 15)]

# ================== UTILS ==================
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

def fmt_usd(x):
    x = float(x or 0)
    s = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1e9: return f"{s}${x/1e9:.2f}B"
    if x >= 1e6: return f"{s}${x/1e6:.1f}M"
    return f"{s}${x:,.0f}"

def num(x):
    try:
        if x is None or x == "": return None
        return float(x)
    except:
        return None

def get_json(url, params=None):
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=25)
        if r.status_code != 200 or not r.text:
            print(f"  API {r.status_code} → {url.split('/')[-1]}")
            return None
        payload = r.json()
        return payload.get("data") if isinstance(payload, dict) else payload
    except Exception as e:
        print("  api error:", e)
        return None

# ================== GEX LEVELS ==================
def obtener_niveles(ticker, fecha, spot=None):
    niveles = {}
    for source in ["oi", "vol"]:
        data = get_json(f"https://api.unusualwhales.com/api/stock/{ticker}/gex-levels",
                        {"date": str(fecha), "source": source})
        if isinstance(data, dict):
            for k, campo in (("CW", "call_wall"), ("PW", "put_wall"),
                             ("QF", "gamma_flip"), ("MAGNET", "gamma_magnet")):
                v = num(data.get(campo))
                if v is not None:
                    if spot and abs(v - spot) / spot > 0.25:
                        continue
                    niveles[f"{k}_{source.upper()}"] = v
    final = {}
    for k in ["CW", "PW", "QF", "MAGNET"]:
        final[k] = niveles.get(f"{k}_OI") or niveles.get(f"{k}_VOL")
    print(f"  Niveles {ticker}: {final}")
    return final

# ================== VOLATILIDAD ==================
def obtener_vol(ticker, fecha):
    out = {"iv": None, "ivr": None, "move1d": None}
    d = get_json(f"https://api.unusualwhales.com/api/stock/{ticker}/volatility/stats", {"date": str(fecha)})
    if isinstance(d, dict):
        out["iv"] = num(d.get("iv"))
        if out["iv"] and out["iv"] < 5: out["iv"] *= 100
        out["ivr"] = num(d.get("iv_rank"))
        if out["ivr"] and out["ivr"] < 3: out["ivr"] *= 100
    return out

# ================== NET PREMIUM ==================
def obtener_net_premium(ticker, fecha):
    rows = get_json(f"https://api.unusualwhales.com/api/stock/{ticker}/net-prem-ticks", {"date": str(fecha)})
    if not isinstance(rows, list) or not rows:
        return {"net_call": 0, "net_put": 0, "flow_ratio": 1.0, "serie": pd.Series(dtype=float)}
    df = pd.DataFrame(rows)
    df["hora"] = pd.to_datetime(df.get("tape_time"), utc=True, errors="coerce").dt.tz_convert(TZ_MERCADO)
    df["net_call"] = pd.to_numeric(df.get("net_call_premium", 0), errors="coerce").fillna(0)
    df["net_put"] = pd.to_numeric(df.get("net_put_premium", 0), errors="coerce").fillna(0)
    df["agres"] = df["net_call"] - df["net_put"]
    total_call = df["net_call"].sum()
    total_put = df["net_put"].sum()
    bullish = max(total_call, 0) + max(-total_put, 0)
    bearish = max(-total_call, 0) + max(total_put, 0)
    ratio = bullish / bearish if bearish > 0 else (2.0 if bullish > 0 else 1.0)
    serie = df.set_index("hora")["agres"].resample("1min").sum().fillna(0)
    return {"net_call": total_call, "net_put": total_put, "flow_ratio": ratio, "serie": serie}

# ================== TAPE ==================
def lado_trade(row):
    tags = str(row.get("tags", "")).lower()
    if "ask_side" in tags: return "COMPRA"
    if "bid_side" in tags: return "VENTA"
    price = pd.to_numeric(row.get("price"), errors="coerce")
    bid = pd.to_numeric(row.get("nbbo_bid"), errors="coerce")
    ask = pd.to_numeric(row.get("nbbo_ask"), errors="coerce")
    if pd.notna(price) and pd.notna(bid) and pd.notna(ask):
        mid = (bid + ask) / 2
        return "COMPRA" if price >= mid else "VENTA"
    return "INDEF"

def procesar_df(data, ticker):
    df = pd.DataFrame(data)
    if df.empty: return df
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

    # Fecha de vencimiento para filtro 0DTE/1DTE
    if "expiry" in df.columns:
        df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
    elif "option_symbol" in df.columns:
        # Intentar extraer expiry del símbolo (formato OCC)
        def extract_expiry(sym):
            try:
                s = str(sym)
                # Buscar 6 dígitos de fecha YYMMDD
                for i in range(len(s)-5):
                    if s[i:i+6].isdigit():
                        return datetime.strptime(s[i:i+6], "%y%m%d").date()
            except:
                return None
            return None
        df["expiry"] = df["option_symbol"].apply(extract_expiry)
    else:
        df["expiry"] = None

    if "option_type" not in df.columns:
        df["option_type"] = df.get("option_symbol", "").astype(str).apply(
            lambda x: "call" if "C" in str(x)[-9:] else "put")
    df["option_type"] = df["option_type"].astype(str).str.lower()
    df["lado"] = df.apply(lado_trade, axis=1)
    df["contrato"] = np.where(df["option_type"].str.contains("call"), "CALL", "PUT")

    signo = []
    for _, r in df.iterrows():
        if r["lado"] == "COMPRA" and r["contrato"] == "CALL": signo.append(1)
        elif r["lado"] == "COMPRA" and r["contrato"] == "PUT": signo.append(-1)
        elif r["lado"] == "VENTA" and r["contrato"] == "CALL": signo.append(-1)
        elif r["lado"] == "VENTA" and r["contrato"] == "PUT": signo.append(1)
        else: signo.append(0)
    df["signo"] = signo
    df["qdelta"] = df["signo"] * df["delta"].abs() * df["size"] * 100
    df["exposicion"] = df["delta"].abs() * df["size"] * 100 * df["spot"].clip(lower=0)
    return df.dropna(subset=["hora"])

def obtener_tape(fecha, ticker):
    partes = []
    url = "https://api.unusualwhales.com/api/option-trades"
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
        time.sleep(0.12)
    if not partes:
        return pd.DataFrame()

    df = procesar_df(partes, ticker).drop_duplicates(subset=["hora", "premium", "size"]).sort_values("hora")

    # ===== FILTRO 0DTE / 1DTE =====
    if SOLO_0DTE and not df.empty and "expiry" in df.columns:
        hoy = fecha
        manana = hoy + timedelta(days=1)
        # Solo contratos que vencen hoy o mañana
        df = df[df["expiry"].isin([hoy, manana])]
        print(f"  → Después de filtro 0DTE/1DTE: {len(df)} trades")

    return df

def cargar_precio(grupo, fecha):
    px = yf.download(YAHOO.get(grupo, grupo), period="7d", interval="1m", progress=False, auto_adjust=True)
    if px.empty:
        px = yf.download(YAHOO.get(grupo, grupo), period="7d", interval="5m", progress=False, auto_adjust=True)
    if px.empty: return px
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    px = px.copy()
    px.index = pd.to_datetime(px.index)
    px.index = px.index.tz_localize("America/New_York") if px.index.tz is None else px.index.tz_convert(TZ_MERCADO)
    return px[px.index.date == fecha].between_time("09:30", "16:00")

# ================== SEÑALES ==================
def leer_senales(last, px_c, niveles, qdelta, expo, grandes, flow_ratio):
    qf = niveles.get("QF")
    pw = niveles.get("PW")
    cw = niveles.get("CW")
    neto = float(qdelta.sum()) if len(qdelta) else 0.0

    if len(qdelta) > 0 and isinstance(qdelta.index, pd.DatetimeIndex):
        q30 = float(qdelta[qdelta.index >= qdelta.index.max() - pd.Timedelta(minutes=30)].sum())
    else:
        q30 = 0.0

    senales = []
    if qf and last < qf and q30 < 0: senales.append("FLUSH")
    if pw and last <= pw * 1.004: senales.append("PISO_PW")
    if cw and last >= cw: senales.append("SOBRE_CW")
    if qf and last >= qf and q30 >= 0: senales.append("RECLAIM_QF")
    if flow_ratio >= 1.4: senales.append("FLOW_BULL")
    elif flow_ratio <= 0.7: senales.append("FLOW_BEAR")
    if not senales: senales.append("NEUTRO")

    if "RECLAIM_QF" in senales or "FLOW_BULL" in senales:
        sesgo = "ALCISTA"
    elif "FLUSH" in senales or "FLOW_BEAR" in senales:
        sesgo = "BAJISTA"
    else:
        sesgo = "NEUTRO / RANGO"
    return sesgo, senales, neto, q30

# ================== GRÁFICO MEJORADO ==================
def grafico(grupo, df, etiqueta, fecha, niveles, vol, net_prem):
    px = cargar_precio(grupo, fecha)
    if px.empty:
        print("  sin precio", grupo)
        return

    bg, fg, grid, linea = "#0b1220", "#e8eef7", "#1d2a3d", "#7eb6ff"
    gold, green, red = "#f1c40f", "#2ecc71", "#e74c3c"
    purple, teal, pink = "#6c7ae0", "#1aa3a3", "#d24b6b"

    umbral = MIN_BURBUJA.get(grupo, 30_000_000)
    if UMBRAL_BURBUJA > 0:
        umbral = UMBRAL_BURBUJA

    px_c = px.copy()
    px_c.index = px_c.index.tz_convert(TZ_VER)
    last = float(px_c["Close"].iloc[-1])
    last_t = px_c.index[-1]

    g = df.copy() if df is not None and not df.empty else pd.DataFrame()
    if not g.empty:
        g["min1"] = g["hora"].dt.tz_convert(TZ_VER).dt.floor("1min")
        expo = g.groupby("min1")["exposicion"].sum()
        qdelta = g.groupby("min1")["qdelta"].sum()
        grandes = g[g["exposicion"] >= umbral].sort_values("exposicion", ascending=False)
        if grandes.empty:
            grandes = g.nlargest(6, "exposicion")
        top = grandes.head(MAX_ETIQUETAS)
    else:
        expo = qdelta = pd.Series(dtype=float)
        grandes = top = g

    agres = net_prem.get("serie", pd.Series(dtype=float))
    flow_ratio = net_prem.get("flow_ratio", 1.0)
    sesgo, senales, neto, q30 = leer_senales(last, px_c, niveles, qdelta, expo, grandes, flow_ratio)

    color_s = green if "ALCISTA" in sesgo else red if "BAJISTA" in sesgo else gold
    regimen = "POSITIVE GEX" if (niveles.get("QF") and last > niveles["QF"]) else "NEGATIVE GEX"

    caja = (
        f"{sesgo}  |  {regimen}\n"
        f"{last_t.strftime('%H:%M')} COL  ·  {grupo} {last:,.2f}\n"
        f"CW {niveles.get('CW','—')}  |  QF {niveles.get('QF','—')}  |  PW {niveles.get('PW','—')}\n"
        f"Flow Ratio {flow_ratio:.2f}  |  qΔ 30m {fmt_usd(q30)}\n"
        f"{', '.join(senales)}"
    )
    print(" ", caja.replace("\n", " | "))

    fig, axs = plt.subplots(4, 1, figsize=(15, 12), sharex=True,
                            gridspec_kw={"height_ratios": [3.6, 0.9, 1.1, 0.9]})
    fig.patch.set_facecolor(bg)
    ax1, axA, axT, axD = axs

    for ax in axs:
        ax.set_facecolor(bg)
        ax.grid(True, color=grid, alpha=0.55)
        ax.tick_params(colors=fg, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(grid)

    ax1.plot(px_c.index, px_c["Close"], color=linea, lw=1.7, zorder=3)
    lo, hi = float(px_c["Close"].min()), float(px_c["Close"].max())
    pad = (hi - lo) * 0.12 or 1
    ax1.set_ylim(lo - pad, hi + pad)
    ax1.set_title(f"{grupo}  |  Flujo Inusual  |  {etiqueta} {fecha}",
                  loc="left", color=fg, fontsize=13, fontweight="bold", pad=10)

    ax1.text(0.01, 0.97, caja, transform=ax1.transAxes, color=color_s,
             fontsize=8.5, fontweight="bold", va="top",
             bbox=dict(boxstyle="round,pad=0.45", fc=bg, ec=color_s, alpha=0.92), zorder=20)

    for k, color, ls in [("CW", purple, "--"), ("QF", teal, "-."), ("PW", pink, "--"), ("MAGNET", gold, ":")]:
        if niveles.get(k):
            y = niveles[k]
            ax1.axhline(y, color=color, ls=ls, lw=1.25, alpha=0.9, zorder=2)
            ax1.text(0.003, y, f" {k} {y:.1f} ", transform=ax1.get_yaxis_transform(),
                     va="center", ha="left", fontsize=8, color="white", fontweight="bold",
                     bbox=dict(fc=color, ec="none", pad=0.3), zorder=15)

    for idx, row in (grandes.iterrows() if not grandes.empty else []):
        i = px.index.get_indexer([row["hora"]], method="nearest")[0]
        x = px_c.index[i]
        y = float(px["Close"].iloc[i])
        put = row["contrato"] == "PUT"
        size = 180 + min(row["exposicion"] / 80000, 420)

        ax1.scatter(x, y, s=size + 90, facecolors="none", edgecolors=gold, linewidths=2.0, zorder=8)
        ax1.scatter(x, y, s=38, c=gold, zorder=9)
        ax1.scatter(x, y, s=55, marker=("v" if put else "^"),
                    c=(red if put else green), zorder=10, edgecolors="black", linewidths=0.6)

        if idx in set(top.index):
            ax1.annotate(fmt_usd(row["exposicion"]),
                         xy=(x, y), xytext=(0, 14 if not put else -16),
                         textcoords="offset points", ha="center",
                         color=gold, fontsize=8.5, fontweight="bold",
                         bbox=dict(boxstyle="round,pad=0.2", fc=bg, ec=gold, alpha=0.85), zorder=12)

    if len(agres):
        vals = agres.values / 1e6
        colors = [green if v >= 0 else red for v in vals]
        axA.bar(agres.index.tz_convert(TZ_VER), vals, width=0.00065, color=colors, alpha=0.85)
    axA.axhline(0, color="#666", lw=0.7)
    axA.set_ylabel("NET PREM $M", fontsize=8, color=fg)

    if len(expo):
        vals = expo.values / 1e6
        colors = [gold if v >= umbral/1e6 else "#3d4f66" for v in vals]
        axT.bar(expo.index, vals, width=0.00065, color=colors, alpha=0.9)
        for t, v in expo.nlargest(4).items():
            if v >= umbral:
                axT.text(t, v/1e6, fmt_usd(v), ha="center", va="bottom",
                         color=gold, fontsize=7.5, fontweight="bold")
    axT.set_ylabel("TOTAL $M", fontsize=8, color=fg)

    if len(qdelta):
        vals = qdelta.values / 1e6
        colors = [green if v >= 0 else red for v in vals]
        axD.bar(qdelta.index, vals, width=0.00065, color=colors, alpha=0.85)
    axD.axhline(0, color="#666", lw=0.7)
    axD.set_ylabel("Q-DELTA $M", fontsize=8, color=fg)
    axD.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    plt.tight_layout()
    ruta = os.path.join(CARPETA, f"{grupo}_Q_{etiqueta}_{fecha}_{datetime.now().strftime('%H%M%S')}.png")
    plt.savefig(ruta, dpi=160, facecolor=bg, bbox_inches="tight")
    plt.close()
    print("Gráfico →", ruta)
    return sesgo

# ================== PROCESAR ==================
def procesar(fecha, etiqueta):
    frames = []
    for grupo, tickers in GRUPOS.items():
        print(f"\n=== {grupo} {etiqueta} {fecha} ===")
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
        niveles = obtener_niveles(tickers[0], fecha, spot)
        vol = obtener_vol(tickers[0], fecha)
        net_prem = obtener_net_premium(tickers[0], fecha)
        grafico(grupo, df, etiqueta, fecha, niveles, vol, net_prem)

    if frames:
        csv = os.path.join(CARPETA, f"tape_{etiqueta}_{fecha}_{datetime.now().strftime('%H%M%S')}.csv")
        pd.concat(frames, ignore_index=True).to_csv(csv, index=False)
        print("CSV →", csv)

def main():
    if not API_KEY:
        print("Falta UW_API_KEY")
        return
    print("=" * 70)
    print("FLUJO QUANT  |  INSTITUCIONAL  |  GEX + FLOW + QDELTA")
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