import os, time, warnings, requests
import pandas as pd, numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
warnings.filterwarnings("ignore")

API_KEY = os.getenv("UW_API_KEY", "")
GRUPOS = {"SPX":["SPX","SPXW"],"SPY":["SPY"],"QQQ":["QQQ"],"IWM":["IWM"],"IBIT":["IBIT"],"GLD":["GLD"]}
YAHOO = {"SPX":"^GSPC","SPY":"SPY","QQQ":"QQQ","IWM":"IWM","IBIT":"IBIT","GLD":"GLD"}
MIN_BURBUJA = {"SPX":150_000_000,"SPY":80_000_000,"QQQ":80_000_000,"IWM":25_000_000,"IBIT":5_000_000,"GLD":15_000_000}
MIN_PREMIUM = int(os.getenv("MIN_PREMIUM","120000"))
SOLO_0DTE = os.getenv("SOLO_0DTE","0")=="1"
UMBRAL_BURBUJA = float(os.getenv("UMBRAL_BURBUJA","30000000"))
LIMIT, MAX_ETIQUETAS = 180, 6
TZ_MERCADO, TZ_VER = ZoneInfo("America/New_York"), ZoneInfo("America/Bogota")
CARPETA = os.getenv("FLUJOS_DIR", os.path.join(os.path.expanduser("~"), "flujos"))
os.makedirs(CARPETA, exist_ok=True)
headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
BLOQUES = [(9,25,11,30),(11,30,13,30),(13,30,15,0),(15,0,16,15)]

def dia_habil_anterior():
    d = datetime.now(TZ_MERCADO).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def fmt_usd(x):
    x = float(x or 0); s = "-" if x < 0 else ""; x = abs(x)
    if x >= 1e9: return f"{s}${x/1e9:.2f}B"
    if x >= 1e6: return f"{s}${x/1e6:.1f}M"
    return f"{s}${x:,.0f}"

def num(x):
    try:
        return None if x in (None,"") else float(x)
    except Exception:
        return None

def get_json(url, params=None):
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=25)
        if r.status_code != 200 or not r.text:
            print("  API", r.status_code, url.split("/")[-1]); return None
        p = r.json()
        return p.get("data") if isinstance(p, dict) else p
    except Exception as e:
        print("  api", e); return None

def obtener_niveles(ticker, fecha, spot=None):
    niveles = {}
    for source in ("oi","vol"):
        data = get_json(f"https://api.unusualwhales.com/api/stock/{ticker}/gex-levels",
                        {"date": str(fecha), "source": source})
        if isinstance(data, dict):
            for k, campo in (("CW","call_wall"),("PW","put_wall"),("QF","gamma_flip"),("MAGNET","gamma_magnet")):
                v = num(data.get(campo))
                if v is None: continue
                if spot and abs(v-spot)/max(abs(spot),1) > 0.25: continue
                niveles[f"{k}_{source.upper()}"] = v
    final = {k: niveles.get(f"{k}_OI") or niveles.get(f"{k}_VOL") for k in ("CW","PW","QF","MAGNET")}
    print("  Niveles", ticker, final)
    return final

def obtener_vol(ticker, fecha):
    out = {"iv": None, "ivr": None}
    d = get_json(f"https://api.unusualwhales.com/api/stock/{ticker}/volatility/stats", {"date": str(fecha)})
    if isinstance(d, dict):
        out["iv"] = num(d.get("iv"));  out["ivr"] = num(d.get("iv_rank"))
        if out["iv"] and out["iv"] < 5: out["iv"] *= 100
        if out["ivr"] and out["ivr"] < 3: out["ivr"] *= 100
    return out

def obtener_net_premium(ticker, fecha):
    rows = get_json(f"https://api.unusualwhales.com/api/stock/{ticker}/net-prem-ticks", {"date": str(fecha)})
    vacio = {"net_call":0,"net_put":0,"flow_ratio":1.0,"serie":pd.Series(dtype=float)}
    if not isinstance(rows, list) or not rows: return vacio
    df = pd.DataFrame(rows)
    df["hora"] = pd.to_datetime(df.get("tape_time"), utc=True, errors="coerce").dt.tz_convert(TZ_MERCADO)
    df["net_call"] = pd.to_numeric(df.get("net_call_premium",0), errors="coerce").fillna(0)
    df["net_put"] = pd.to_numeric(df.get("net_put_premium",0), errors="coerce").fillna(0)
    df["agres"] = df["net_call"] - df["net_put"]
    tc, tp = df["net_call"].sum(), df["net_put"].sum()
    bull = max(tc,0)+max(-tp,0); bear = max(-tc,0)+max(tp,0)
    ratio = bull/bear if bear>0 else (2.0 if bull>0 else 1.0)
    serie = df.dropna(subset=["hora"]).set_index("hora")["agres"].resample("1min").sum().fillna(0)
    return {"net_call":tc,"net_put":tp,"flow_ratio":ratio,"serie":serie}

def lado_trade(row):
    tags = str(row.get("tags","")).lower()
    if "ask_side" in tags: return "COMPRA"
    if "bid_side" in tags: return "VENTA"
    price = pd.to_numeric(row.get("price"), errors="coerce")
    bid = pd.to_numeric(row.get("nbbo_bid"), errors="coerce")
    ask = pd.to_numeric(row.get("nbbo_ask"), errors="coerce")
    if pd.notna(price) and pd.notna(bid) and pd.notna(ask):
        return "COMPRA" if price >= (bid+ask)/2 else "VENTA"
    return "INDEF"

def procesar_df(data, ticker):
    df = pd.DataFrame(data)
    if df.empty: return df
    df["origen"] = ticker
    raw = df["executed_at"] if "executed_at" in df.columns else df.get("created_at")
    try:
        s = str(raw.iloc[0]) if raw is not None and len(raw) else ""
        if s.replace(".", "", 1).isdigit():
            df["hora"] = pd.to_datetime(pd.to_numeric(raw, errors="coerce"), unit="ms", utc=True, errors="coerce")
        else:
            df["hora"] = pd.to_datetime(raw, utc=True, errors="coerce")
    except Exception:
        df["hora"] = pd.to_datetime(raw, utc=True, errors="coerce")
    df["hora"] = df["hora"].dt.tz_convert(TZ_MERCADO)
    for c, default in (("premium",0),("delta",0),("size",0),("underlying_price",0)):
        df[c if c!="underlying_price" else "spot"] = pd.to_numeric(df.get(c, default), errors="coerce").fillna(0)
    if "spot" not in df.columns:
        df["spot"] = pd.to_numeric(df.get("underlying_price",0), errors="coerce").fillna(0)
    df["option_type"] = df.get("option_type", "put").astype(str).str.lower()
    df["lado"] = df.apply(lado_trade, axis=1)
    df["contrato"] = np.where(df["option_type"].str.contains("call"), "CALL", "PUT")
    signo = []
    for _, r in df.iterrows():
        if r["lado"]=="COMPRA" and r["contrato"]=="CALL": signo.append(1)
        elif r["lado"]=="COMPRA" and r["contrato"]=="PUT": signo.append(-1)
        elif r["lado"]=="VENTA" and r["contrato"]=="CALL": signo.append(-1)
        elif r["lado"]=="VENTA" and r["contrato"]=="PUT": signo.append(1)
        else: signo.append(0)
    df["signo"] = signo
    df["qdelta"] = df["signo"] * df["delta"].abs() * df["size"] * 100
    df["exposicion"] = df["delta"].abs() * df["size"] * 100 * df["spot"].clip(lower=0)
    return df.dropna(subset=["hora"])

def obtener_tape(fecha, ticker):
    partes, url = [], "https://api.unusualwhales.com/api/option-trades"
    for h1,m1,h2,m2 in BLOQUES:
        ini = datetime(fecha.year,fecha.month,fecha.day,h1,m1,tzinfo=TZ_MERCADO).astimezone(ZoneInfo("UTC"))
        fin = datetime(fecha.year,fecha.month,fecha.day,h2,m2,tzinfo=TZ_MERCADO).astimezone(ZoneInfo("UTC"))
        data = get_json(url, {"ticker_symbol":ticker,"newer_than":ini.strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "older_than":fin.strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "min_premium":MIN_PREMIUM,"limit":LIMIT})
        data = data if isinstance(data, list) else []
        print(f"  {ticker} {h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}: {len(data)}")
        partes.extend(data); time.sleep(0.12)
    if not partes: return pd.DataFrame()
    return procesar_df(partes, ticker).drop_duplicates(subset=["hora","premium","size"]).sort_values("hora")

def cargar_precio(grupo, fecha):
    px = yf.download(YAHOO.get(grupo,grupo), period="7d", interval="1m", progress=False, auto_adjust=True)
    if px.empty:
        px = yf.download(YAHOO.get(grupo,grupo), period="7d", interval="5m", progress=False, auto_adjust=True)
    if px.empty: return px
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    px.index = pd.to_datetime(px.index)
    px.index = px.index.tz_localize("America/New_York") if px.index.tz is None else px.index.tz_convert(TZ_MERCADO)
    return px[px.index.date == fecha].between_time("09:30","16:00")

def grafico(grupo, df, etiqueta, fecha, niveles, vol, net_prem):
    px = cargar_precio(grupo, fecha)
    if px.empty:
        print("  sin precio", grupo); return
    bg, fg, grid = "#0b1220", "#e8eef7", "#1d2a3d"
    last = float(px["Close"].iloc[-1])
    umbral = MIN_BURBUJA.get(grupo, 30_000_000)
    if UMBRAL_BURBUJA > 0: umbral = max(umbral, UMBRAL_BURBUJA) if grupo=="SPX" else umbral
    fig, (ax1, ax2) = plt.subplots(2,1, figsize=(13,7.2), sharex=True,
                                   gridspec_kw={"height_ratios":[3.1,1]}, facecolor=bg)
    for ax in (ax1,ax2):
        ax.set_facecolor(bg); ax.tick_params(colors=fg); ax.grid(True, color=grid)
        for s in ax.spines.values(): s.set_color(grid)
    ax1.plot(px.index, px["Close"], color="#7eb6ff", lw=1.4)
    if df is not None and not df.empty:
        grandes = df[df["exposicion"]>=umbral].copy()
        if grandes.empty: grandes = df.nlargest(6,"exposicion")
        top = grandes.nlargest(MAX_ETIQUETAS,"exposicion")
        for _, r in grandes.iterrows():
            col = "#2ecc71" if r["signo"]>0 else "#e74c3c"
            m = "^" if r["signo"]>0 else "v"
            sz = 40 + min(r["exposicion"]/umbral, 6)*18
            ax1.scatter(r["hora"], r["spot"] if r["spot"] else last, s=sz, c=col, marker=m, zorder=5, alpha=0.85)
        for _, r in top.iterrows():
            ax1.annotate(fmt_usd(r["exposicion"]), (r["hora"], r["spot"] if r["spot"] else last),
                         textcoords="offset points", xytext=(4,8), color=fg, fontsize=7)
        qdelta = df.set_index("hora")["qdelta"]
        neto = float(qdelta.sum())
    else:
        neto = 0.0
    for k, col in (("PW","#e74c3c"),("QF","#f1c40f"),("CW","#2ecc71")):
        if niveles.get(k):
            ax1.axhline(niveles[k], color=col, ls="--", lw=1)
            ax1.text(0.01, niveles[k], f"{k} {niveles[k]:.2f}", transform=ax1.get_yaxis_transform(),
                     color="white", fontsize=8, va="bottom", bbox=dict(fc=col, ec="none", pad=0.2))
    serie = net_prem.get("serie", pd.Series(dtype=float))
    if len(serie):
        ax2.bar(serie.index, serie.values/1e9, width=0.0007,
                color=["#2ecc71" if v>=0 else "#e74c3c" for v in serie.values], alpha=0.8)
    ax2.axhline(0, color=fg, lw=0.5)
    ax2.set_ylabel("$B agresor", color=fg)
    ratio = net_prem.get("flow_ratio", 1)
    sesgo = "ALCISTA" if ratio>=1.4 or neto>0 else "BAJISTA" if ratio<=0.7 or neto<0 else "NEUTRO"
    iv = f"IV {vol['iv']:.1f}%" if vol.get("iv") else ""
    ax1.set_title(f"{grupo} {etiqueta} {fecha} | {sesgo} | qΔ {fmt_usd(neto)} {iv}", color=fg, loc="left")
    ax1.set_ylabel("Precio", color=fg)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ_MERCADO))
    fig.text(0.01, 0.01, f"NY {datetime.now(TZ_MERCADO):%H:%M}  |  COL {datetime.now(TZ_VER):%H:%M}  |  print≠dirección ETF",
             color="#9aa7b8", fontsize=8)
    fig.tight_layout()
    ruta = os.path.join(CARPETA, f"{grupo}_{etiqueta}_{fecha}_{datetime.now(TZ_MERCADO):%H%M%S}.png")
    fig.savefig(ruta, dpi=110, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    print("  PNG", ruta)

def procesar(fecha, etiqueta):
    print("====", etiqueta, fecha)
    for grupo, ticks in GRUPOS.items():
        frames = []
        for tk in ticks:
            t = obtener_tape(fecha, tk)
            if not t.empty: frames.append(t)
            time.sleep(0.1)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        spot = None
        px = cargar_precio(grupo, fecha)
        if not px.empty: spot = float(px["Close"].iloc[-1])
        niv = {}
        for tk in ticks:
            n = obtener_niveles(tk, fecha, spot)
            for k,v in n.items():
                if v is not None and k not in niv: niv[k] = v
        vol = obtener_vol(ticks[0], fecha)
        net = obtener_net_premium(ticks[0], fecha)
        if grupo=="SPX" and "SPXW" in ticks:
            net2 = obtener_net_premium("SPXW", fecha)
            net["net_call"] += net2["net_call"]; net["net_put"] += net2["net_put"]
        grafico(grupo, df, etiqueta, fecha, niv, vol, net)

def main():
    if not API_KEY:
        print("Falta UW_API_KEY"); return
    modo = os.getenv("MODO_FLUJO","AMBOS").upper()
    hoy, ayer = datetime.now(TZ_MERCADO).date(), dia_habil_anterior()
    print("MODO", modo, "HOY", hoy, "AYER", ayer)
    if modo in ("AYER","AMBOS"): procesar(ayer, "AYER")
    if modo in ("HOY","AMBOS"): procesar(hoy, "HOY")

if __name__ == "__main__":
    main()