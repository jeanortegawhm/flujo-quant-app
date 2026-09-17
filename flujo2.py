import os, json, time, warnings, requests
import pandas as pd
import numpy as np
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

MIN_PREMIUM = int(os.getenv("MIN_PREMIUM", "120000"))
SOLO_0DTE = os.getenv("SOLO_0DTE", "0") == "1"
UMBRAL_BURBUJA = float(os.getenv("UMBRAL_BURBUJA", "0") or 0)
ALERTA_USD = float(os.getenv("ALERTA_USD", "2000000"))
FIG_ANCHO = float(os.getenv("FIG_ANCHO", "12.4"))
FIG_ALTO = float(os.getenv("FIG_ALTO", "13.6"))
FRANJA_ALTO = float(os.getenv("FRANJA_ALTO", "0.62"))
FRANJA_MIN = float(os.getenv("FRANJA_MIN", "1"))
PRECIO_ALTO = float(os.getenv("PRECIO_ALTO", "3.0"))
TOTAL_ALTO = float(os.getenv("TOTAL_ALTO", "1.05"))
QDELTA_ALTO = float(os.getenv("QDELTA_ALTO", "1.05"))
GEX_ALTO = float(os.getenv("GEX_ALTO", "1.25"))
DPI = int(os.getenv("DPI_FIG", "118"))

LIMIT, MAX_ETIQUETAS = 200, 5
TZ, TZ_COL = ZoneInfo("America/New_York"), ZoneInfo("America/Bogota")
CARPETA = os.getenv("FLUJOS_DIR", os.path.join(os.path.expanduser("~"), "flujos"))
os.makedirs(CARPETA, exist_ok=True)
headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
BLOQUES = [(9,25,11,0),(11,0,12,30),(12,30,14,0),(14,0,15,15),(15,15,16,15)]

def ayer():
    d = datetime.now(TZ).date() - timedelta(days=1)
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
        return None if x in (None, "") else float(x)
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

def telegram(msg):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN",""), os.getenv("TELEGRAM_CHAT_ID","")
    if not tok or not chat: return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": msg}, timeout=12)
    except Exception:
        pass

def dte_de(row, fecha):
    exp = row.get("expiry")
    if pd.isna(exp) if not isinstance(exp, (datetime, pd.Timestamp)) else False:
        exp = None
    if exp is None:
        return ""
    try:
        if hasattr(exp, "date"):
            exp = exp.date()
        return f"{max((exp - fecha).days, 0)}d"
    except Exception:
        return ""

def estilo(row):
    tags = str(row.get("tags", "") or "").lower()
    if "sweep" in tags: return "SWP"
    if "block" in tags: return "BLK"
    return "PRT"

def niveles(tk, fecha, spot=None):
    raw = {}
    for src in ("oi", "vol"):
        d = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
                     {"date": str(fecha), "source": src})
        if not isinstance(d, dict):
            continue
        for k, c in (("CW","call_wall"),("PW","put_wall"),("QF","gamma_flip"),("MAGNET","gamma_magnet")):
            v = num(d.get(c))
            if v is None: continue
            if spot and abs(v - spot) / max(abs(spot), 1) > 0.035: continue
            raw[f"{k}_{src.upper()}"] = v
    return {k: raw.get(f"{k}_OI") or raw.get(f"{k}_VOL") for k in ("CW","PW","QF","MAGNET")}

def gex_por_strike(tk, fecha, spot):
    """Barras de GEX cerca del spot. Si el endpoint no existe, panel vacío."""
    candidatos = [
        (f"https://api.unusualwhales.com/api/stock/{tk}/spot-exposures", {"date": str(fecha)}),
        (f"https://api.unusualwhales.com/api/stock/{tk}/gex", {"date": str(fecha)}),
        (f"https://api.unusualwhales.com/api/stock/{tk}/greek-exposure", {"date": str(fecha)}),
    ]
    rows = None
    for url, params in candidatos:
        data = get_json(url, params)
        if isinstance(data, list) and data:
            rows = data; break
        if isinstance(data, dict):
            for k in ("data", "gex", "exposures", "strikes"):
                if isinstance(data.get(k), list) and data.get(k):
                    rows = data[k]; break
        if rows: break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    col_k = next((c for c in ("strike","strike_price","k") if c in df.columns), None)
    if not col_k:
        return pd.DataFrame()
    df["strike"] = pd.to_numeric(df[col_k], errors="coerce")
    gcol = next((c for c in ("gex","gamma","call_gex","net_gex","dex") if c in df.columns), None)
    pcol = next((c for c in ("put_gex","put_gamma") if c in df.columns), None)
    ccol = next((c for c in ("call_gex","call_gamma") if c in df.columns), None)
    if gcol:
        df["gex"] = pd.to_numeric(df[gcol], errors="coerce").fillna(0)
    elif ccol or pcol:
        df["gex"] = pd.to_numeric(df.get(ccol, 0), errors="coerce").fillna(0) - \
                    pd.to_numeric(df.get(pcol, 0), errors="coerce").fillna(0).abs()
    else:
        return pd.DataFrame()
    df = df.dropna(subset=["strike"])
    if spot:
        df = df[abs(df["strike"] - spot) / max(abs(spot), 1) <= 0.04]
    return df.sort_values("strike")

def vol_stats(tk, fecha):
    out = {"iv": None}
    d = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/volatility/stats", {"date": str(fecha)})
    if isinstance(d, dict):
        out["iv"] = num(d.get("iv"))
        if out["iv"] and out["iv"] < 5: out["iv"] *= 100
    return out

def net_prem(tk, fecha):
    vac = {"net_call":0,"net_put":0,"flow_ratio":1.0,"serie":pd.Series(dtype=float)}
    rows = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/net-prem-ticks", {"date": str(fecha)})
    if not isinstance(rows, list) or not rows: return vac
    df = pd.DataFrame(rows)
    df["hora"] = pd.to_datetime(df.get("tape_time"), utc=True, errors="coerce").dt.tz_convert(TZ)
    df["net_call"] = pd.to_numeric(df.get("net_call_premium",0), errors="coerce").fillna(0)
    df["net_put"] = pd.to_numeric(df.get("net_put_premium",0), errors="coerce").fillna(0)
    df["agres"] = df["net_call"] - df["net_put"]
    tc, tp = float(df["net_call"].sum()), float(df["net_put"].sum())
    bull = max(tc,0)+max(-tp,0); bear = max(-tc,0)+max(tp,0)
    ratio = bull/bear if bear else (2 if bull else 1)
    serie = df.dropna(subset=["hora"]).set_index("hora")["agres"].resample("1min").sum().fillna(0)
    return {"net_call":tc,"net_put":tp,"flow_ratio":ratio,"serie":serie}

def lado(row):
    tags = str(row.get("tags","") or "").lower()
    if "ask_side" in tags: return "COMPRA"
    if "bid_side" in tags: return "VENTA"
    p = pd.to_numeric(row.get("price"), errors="coerce")
    b = pd.to_numeric(row.get("nbbo_bid"), errors="coerce")
    a = pd.to_numeric(row.get("nbbo_ask"), errors="coerce")
    if pd.notna(p) and pd.notna(b) and pd.notna(a) and a > b:
        return "COMPRA" if p >= (b+a)/2 else "VENTA"
    return "INDEF"

def procesar_df(data, ticker):
    df = pd.DataFrame(data)
    if df.empty: return df
    raw = df["executed_at"] if "executed_at" in df.columns else df.get("created_at")
    try:
        s0 = str(raw.iloc[0]) if raw is not None and len(raw) else ""
        if s0.replace(".", "", 1).isdigit():
            df["hora"] = pd.to_datetime(pd.to_numeric(raw, errors="coerce"), unit="ms", utc=True, errors="coerce")
        else:
            df["hora"] = pd.to_datetime(raw, utc=True, errors="coerce")
    except Exception:
        df["hora"] = pd.to_datetime(raw, utc=True, errors="coerce")
    df["hora"] = df["hora"].dt.tz_convert(TZ)
    df["premium"] = pd.to_numeric(df.get("premium",0), errors="coerce").fillna(0)
    df["delta"] = pd.to_numeric(df.get("delta",0), errors="coerce").fillna(0)
    df["size"] = pd.to_numeric(df.get("size",0), errors="coerce").fillna(0)
    df["spot"] = pd.to_numeric(df.get("underlying_price",0), errors="coerce").fillna(0)
    if "expiry" in df.columns:
        df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
    df["option_type"] = df.get("option_type","put").astype(str).str.lower()
    df["lado"] = df.apply(lado, axis=1)
    df["contrato"] = np.where(df["option_type"].str.contains("call"), "C", "P")
    df["estilo"] = df.apply(estilo, axis=1)
    sg = []
    for _, r in df.iterrows():
        if r["lado"]=="COMPRA" and r["contrato"]=="C": sg.append(1)
        elif r["lado"]=="COMPRA" and r["contrato"]=="P": sg.append(-1)
        elif r["lado"]=="VENTA" and r["contrato"]=="C": sg.append(-1)
        elif r["lado"]=="VENTA" and r["contrato"]=="P": sg.append(1)
        else: sg.append(0)
    df["signo"] = sg
    df["qdelta"] = df["signo"] * df["delta"].abs() * df["size"] * 100
    df["exposicion"] = df["delta"].abs() * df["size"] * 100 * df["spot"].clip(lower=0)
    df["origen"] = ticker
    return df.dropna(subset=["hora"])

def tape(fecha, ticker):
    out = []
    for h1,m1,h2,m2 in BLOQUES:
        a = datetime(fecha.year,fecha.month,fecha.day,h1,m1,tzinfo=TZ).astimezone(ZoneInfo("UTC"))
        b = datetime(fecha.year,fecha.month,fecha.day,h2,m2,tzinfo=TZ).astimezone(ZoneInfo("UTC"))
        data = get_json("https://api.unusualwhales.com/api/option-trades", {
            "ticker_symbol": ticker,
            "newer_than": a.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "older_than": b.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "min_premium": MIN_PREMIUM, "limit": LIMIT,
        })
        data = data if isinstance(data, list) else []
        print(f"  {ticker} {h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}: {len(data)}")
        out.extend(data); time.sleep(0.12)
    if not out: return pd.DataFrame()
    df = procesar_df(out, ticker).drop_duplicates(["hora","premium","size"]).sort_values("hora")
    if SOLO_0DTE and "expiry" in df.columns:
        df = df[df["expiry"].isin([fecha, fecha + timedelta(days=1)])]
    return df

def precio(grupo, fecha):
    px = yf.download(YAHOO[grupo], period="7d", interval="1m", progress=False, auto_adjust=True)
    if px.empty:
        px = yf.download(YAHOO[grupo], period="7d", interval="5m", progress=False, auto_adjust=True)
    if px.empty: return px
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    px.index = pd.to_datetime(px.index)
    px.index = px.index.tz_localize("America/New_York") if px.index.tz is None else px.index.tz_convert(TZ)
    return px[px.index.date == fecha].between_time("09:30","16:00")

def grafico(grupo, df, etiqueta, fecha, niv, vol, net, gex_df):
    px = precio(grupo, fecha)
    if px.empty:
        print("  sin precio", grupo); return None
    bg, fg, grid, gold = "#0b1220", "#e8eef7", "#1d2a3d", "#d4af37"
    last = float(px["Close"].iloc[-1])
    umbral = UMBRAL_BURBUJA if UMBRAL_BURBUJA > 0 else MIN_BURBUJA.get(grupo, 30_000_000)

    fig = plt.figure(figsize=(FIG_ANCHO, FIG_ALTO), facecolor=bg)
    gs = fig.add_gridspec(
        5, 1, hspace=0.08,
        height_ratios=[PRECIO_ALTO, FRANJA_ALTO, TOTAL_ALTO, QDELTA_ALTO, GEX_ALTO],
    )
    ax1 = fig.add_subplot(gs[0])
    axA = fig.add_subplot(gs[1], sharex=ax1)
    axT = fig.add_subplot(gs[2], sharex=ax1)
    axQ = fig.add_subplot(gs[3], sharex=ax1)
    axG = fig.add_subplot(gs[4])
    for ax in (ax1, axA, axT, axQ, axG):
        ax.set_facecolor(bg)
        ax.tick_params(colors=fg, labelsize=8)
        ax.grid(True, color=grid, alpha=0.28)
        for s in ax.spines.values():
            s.set_color(grid)
    axA.grid(False)
    plt.setp(ax1.get_xticklabels(), visible=False)
    plt.setp(axA.get_xticklabels(), visible=False)
    plt.setp(axT.get_xticklabels(), visible=False)

    ax1.plot(px.index, px["Close"], color="#6ea8ff", lw=1.55)
    ax1.fill_between(px.index, px["Close"], px["Close"].min(), color="#6ea8ff", alpha=0.07)
    lo = float(px["Low"].min()) if "Low" in px.columns else float(px["Close"].min())
    hi = float(px["High"].max()) if "High" in px.columns else float(px["Close"].max())
    extras = [v for v in niv.values() if v]
    if extras:
        lo, hi = min(lo, min(extras)), max(hi, max(extras))
    pad = (hi - lo) * 0.08 or 1
    ax1.set_ylim(lo - pad, hi + pad)

    neto, grandes = 0.0, pd.DataFrame()
    if df is not None and not df.empty:
        grandes = df[df["exposicion"] >= umbral]
        if grandes.empty:
            grandes = df.nlargest(6, "exposicion")
        top = grandes.nlargest(MAX_ETIQUETAS, "exposicion")
        for _, r in grandes.iterrows():
            y = r["spot"] if r["spot"] else last
            col = "#2ecc71" if r["signo"] > 0 else "#e74c3c"
            ax1.scatter(r["hora"], y, s=200, facecolors="none", edgecolors=gold, lw=1.6, zorder=6)
            ax1.scatter(r["hora"], y, s=26, c=gold, zorder=7)
            ax1.scatter(r["hora"], y, s=34, c=col, marker="^" if r["signo"]>0 else "v", zorder=8)
        for _, r in top.iterrows():
            y = r["spot"] if r["spot"] else last
            txt = f"{fmt_usd(r['exposicion'])} {r['contrato']} {dte_de(r, fecha)} {r['estilo']}"
            ax1.annotate(txt, (r["hora"], y), textcoords="offset points", xytext=(6, 9),
                         color=gold, fontsize=7.5, fontweight="bold")
        neto = float(df["qdelta"].sum())

    for k, col in (("PW","#e74c3c"),("QF","#1aa3a3"),("CW","#2ecc71"),("MAGNET","#9b59b6")):
        v = niv.get(k)
        if not v: continue
        ax1.axhline(v, color=col, ls="--", lw=1.1)
        ax1.text(px.index[0], v, f" {k} {v:.2f} ", color="white", fontsize=8,
                 fontweight="bold", va="bottom", bbox=dict(fc=col, ec="none", pad=0.2))
    ax1.text(1.0, last, f" {last:,.2f} ", transform=ax1.get_yaxis_transform(),
             color="white", fontsize=8, va="center", ha="left",
             bbox=dict(fc="#3d5afe", ec="none", pad=0.22))
    ax1.set_ylabel("PRECIO", color=fg, fontsize=8)

    serie = net.get("serie", pd.Series(dtype=float))
    axA.set_ylim(0, 1); axA.set_yticks([]); axA.set_ylabel("AGRESOR", color=fg, fontsize=7)
    if len(serie):
        vals = serie.values.astype(float)
        mx = np.percentile(np.abs(vals), 90) or 1
        paso = pd.Timedelta(minutes=max(FRANJA_MIN, 1))
        for ts, v in zip(serie.index, vals):
            n = max(min(v / mx, 1), -1)
            c = "#d4af37" if n>=0.15 else "#2aa3a1" if n>=0 else "#7e57c2" if n<=-0.15 else "#5b7c99"
            axA.axvspan(ts, ts + paso, ymin=0, ymax=1, color=c, lw=0, alpha=0.95)

    if not grandes.empty:
        axT.bar(grandes["hora"], grandes["exposicion"]/1e6, width=0.003, color=gold, alpha=0.92)
        for _, r in grandes.nlargest(4, "exposicion").iterrows():
            axT.annotate(fmt_usd(r["exposicion"]), (r["hora"], r["exposicion"]/1e6),
                         textcoords="offset points", xytext=(0, 5), ha="center", color=gold, fontsize=7)
    axT.set_ylabel("TOTAL $M", color=fg, fontsize=7)

    if df is not None and not df.empty:
        qd = df.set_index("hora")["qdelta"].resample("2min").sum().fillna(0)
        axQ.bar(qd.index, qd.values/1e6, width=0.0014,
                color=["#2ecc71" if v>=0 else "#e74c3c" for v in qd.values])
    axQ.axhline(0, color=fg, lw=0.5)
    axQ.set_ylabel("QΔ $M", color=fg, fontsize=7)
    axQ.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ))

    if gex_df is not None and not gex_df.empty:
        cols = ["#2ecc71" if v >= 0 else "#e74c3c" for v in gex_df["gex"]]
        axG.bar(gex_df["strike"], gex_df["gex"], color=cols, width=max((gex_df["strike"].max()-gex_df["strike"].min())/40, 0.2))
        axG.axvline(last, color="#6ea8ff", ls="--", lw=1)
        axG.set_xlabel("Strike", color=fg, fontsize=8)
    else:
        axG.text(0.5, 0.5, "GEX strike no disponible en este plan/API",
                 transform=axG.transAxes, ha="center", color="#8b9bb0", fontsize=8)
    axG.set_ylabel("GEX strike", color=fg, fontsize=7)

    ratio = float(net.get("flow_ratio", 1) or 1)
    qf = niv.get("QF")
    regimen = "GEX+" if qf and last >= qf else "GEX-" if qf else "GEX?"
    sesgo = "ALCISTA" if ratio >= 1.4 else "BAJISTA" if ratio <= 0.7 else "NEUTRO"
    iv = f"  IV {vol['iv']:.1f}%" if vol.get("iv") else ""
    ax1.set_title(f"{grupo}  {etiqueta}  {fecha}   |   {regimen} {sesgo}   |   qΔ {fmt_usd(neto)}   flow {ratio:.2f}{iv}",
                  color=fg, loc="left", fontsize=11, pad=6)
    fig.text(0.01, 0.008, f"NY {datetime.now(TZ):%H:%M}  COL {datetime.now(TZ_COL):%H:%M}  "
             f"etiqueta = $ C/P DTE SWP|BLK  |  print ≠ dirección ETF",
             color="#8b9bb0", fontsize=8)
    fig.tight_layout(rect=[0, 0.025, 1, 1])
    ruta = os.path.join(CARPETA, f"{grupo}_{etiqueta}_{fecha}_{datetime.now(TZ):%H%M%S}.png")
    fig.savefig(ruta, dpi=DPI, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    print("  PNG", ruta)
    return {"ticker": grupo, "modo": etiqueta, "fecha": str(fecha), "regimen": regimen,
            "sesgo": sesgo, "qdelta": neto, "flow_ratio": ratio,
            "cw": niv.get("CW"), "pw": niv.get("PW"), "qf": niv.get("QF")}

def procesar(fecha, etiqueta, resumen):
    print("====", etiqueta, fecha)
    for grupo, ticks in GRUPOS.items():
        frames = []
        for tk in ticks:
            t = tape(fecha, tk)
            if t is not None and not t.empty:
                frames.append(t)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        px = precio(grupo, fecha)
        spot = float(px["Close"].iloc[-1]) if not px.empty else None
        niv, gex = {}, pd.DataFrame()
        for tk in ticks:
            for k, v in niveles(tk, fecha, spot).items():
                if v is not None and k not in niv:
                    niv[k] = v
            if gex.empty:
                gex = gex_por_strike(tk, fecha, spot)
        vol = vol_stats(ticks[0], fecha)
        net = net_prem(ticks[0], fecha)
        if grupo == "SPX" and "SPXW" in ticks:
            n2 = net_prem("SPXW", fecha)
            net["net_call"] += n2["net_call"]; net["net_put"] += n2["net_put"]
            if len(n2["serie"]):
                net["serie"] = net["serie"].add(n2["serie"], fill_value=0)
        card = grafico(grupo, df, etiqueta, fecha, niv, vol, net, gex)
        if card: resumen.append(card)
        if df is not None and not df.empty:
            for _, r in df.nlargest(3, "exposicion").iterrows():
                if r["exposicion"] >= ALERTA_USD:
                    telegram(f"{grupo} {etiqueta} {fmt_usd(r['exposicion'])} {r['contrato']} {r['estilo']}")

def main():
    if not API_KEY:
        print("Falta UW_API_KEY"); return
    modo = os.getenv("MODO_FLUJO", "AMBOS").upper()
    resumen = []
    if modo in ("AYER","AMBOS"): procesar(ayer(), "AYER", resumen)
    if modo in ("HOY","AMBOS"): procesar(datetime.now(TZ).date(), "HOY", resumen)
    with open(os.path.join(CARPETA, "resumen.json"), "w") as f:
        json.dump(resumen, f, default=str)

if __name__ == "__main__":
    main()
