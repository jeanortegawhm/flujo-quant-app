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
MIN_BURBUJA = {"SPX":4_000_000,"SPY":1_500_000,"QQQ":1_500_000,"IWM":800_000,"IBIT":600_000,"GLD":800_000}

MIN_PREMIUM = int(os.getenv("MIN_PREMIUM", "250000"))
SOLO_0DTE = os.getenv("SOLO_0DTE", "0") == "1"
UMBRAL_BURBUJA = float(os.getenv("UMBRAL_BURBUJA", "0") or 0)
ALERTA_USD = float(os.getenv("ALERTA_USD", "2000000"))
FIG_ANCHO = float(os.getenv("FIG_ANCHO", "12.4"))
FIG_ALTO = float(os.getenv("FIG_ALTO", "12.2"))
FRANJA_ALTO = float(os.getenv("FRANJA_ALTO", "0.28"))
FRANJA_MIN = float(os.getenv("FRANJA_MIN", "4"))
PRECIO_ALTO = float(os.getenv("PRECIO_ALTO", "3.5"))
QD_ALTO = float(os.getenv("QD_ALTO", "1.15"))
TOTAL_ALTO = float(os.getenv("TOTAL_ALTO", "1.15"))
DPI = int(os.getenv("DPI_FIG", "118"))
MAX_DTE = int(os.getenv("MAX_DTE", "5"))

LIMIT, MAX_ETIQUETAS = 200, 8
TZ, TZ_COL = ZoneInfo("America/New_York"), ZoneInfo("America/Bogota")
CARPETA = os.getenv("FLUJOS_DIR", os.path.join(os.path.expanduser("~"), "flujos"))
os.makedirs(CARPETA, exist_ok=True)
headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
BLOQUES = [(9,30,10,15),(10,15,11,15),(11,15,12,30),(12,30,14,0),(14,0,15,15),(15,15,16,5)]

def ayer():
    d = datetime.now(TZ).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def sesion(fecha):
    a = datetime(fecha.year, fecha.month, fecha.day, 9, 30, tzinfo=TZ)
    b = datetime(fecha.year, fecha.month, fecha.day, 16, 0, tzinfo=TZ)
    return a, b

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
    if exp is None or (isinstance(exp, float) and np.isnan(exp)):
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
            if v is not None:
                raw[f"{k}_{src.upper()}"] = v
    return {k: raw.get(f"{k}_OI") or raw.get(f"{k}_VOL") for k in ("CW","PW","QF","MAGNET")}

def vol_extra(tk, fecha):
    out = {"iv": None, "ivp": None, "ivr": None, "imp_move_pct": None}
    rows = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/interpolated-iv", {"date": str(fecha)})
    if isinstance(rows, list) and rows:
        d1 = next((r for r in rows if num(r.get("days")) == 1), None)
        d30 = next((r for r in rows if num(r.get("days")) == 30), rows[-1])
        if d1:
            out["imp_move_pct"] = num(d1.get("implied_move_perc"))
        if d30:
            out["iv"] = num(d30.get("volatility"))
            out["ivp"] = num(d30.get("percentile"))
    if out["iv"] and out["iv"] < 3:
        out["iv"] *= 100
    return out

def net_prem(tk, fecha):
    vac = {"net_call": 0, "net_put": 0, "flow_ratio": 1.0, "serie": pd.Series(dtype=float)}
    rows = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/net-prem-ticks", {"date": str(fecha)})
    if not isinstance(rows, list) or not rows:
        return vac
    df = pd.DataFrame(rows)
    df["hora"] = pd.to_datetime(df.get("tape_time"), utc=True, errors="coerce").dt.tz_convert(TZ)
    df["net_call"] = pd.to_numeric(df.get("net_call_premium", 0), errors="coerce").fillna(0)
    df["net_put"] = pd.to_numeric(df.get("net_put_premium", 0), errors="coerce").fillna(0)
    df["agres"] = df["net_call"] - df["net_put"]
    tc, tp = float(df["net_call"].sum()), float(df["net_put"].sum())
    bull = max(tc, 0) + max(-tp, 0)
    bear = max(-tc, 0) + max(tp, 0)
    ratio = bull / bear if bear else (2 if bull else 1)
    mins = max(int(FRANJA_MIN), 3)
    a, b = sesion(fecha)
    serie = (df.dropna(subset=["hora"]).set_index("hora")["agres"]
             .resample(f"{mins}min").sum().fillna(0))
    serie = serie[(serie.index >= a) & (serie.index <= b)]
    return {"net_call": tc, "net_put": tp, "flow_ratio": ratio, "serie": serie}

def lado(row):
    tags = str(row.get("tags", "") or "").lower()
    if "ask_side" in tags:
        return "COMPRA"
    if "bid_side" in tags:
        return "VENTA"
    p = pd.to_numeric(row.get("price"), errors="coerce")
    bid = pd.to_numeric(row.get("nbbo_bid"), errors="coerce")
    ask = pd.to_numeric(row.get("nbbo_ask"), errors="coerce")
    if pd.notna(p) and pd.notna(bid) and pd.notna(ask) and ask > bid:
        return "COMPRA" if p >= (bid + ask) / 2 else "VENTA"
    return "INDEF"

def procesar_df(data, ticker):
    df = pd.DataFrame(data)
    if df.empty:
        return df
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
    df["premium"] = pd.to_numeric(df.get("premium", 0), errors="coerce").fillna(0)
    df["spot"] = pd.to_numeric(df.get("underlying_price", 0), errors="coerce").fillna(0)
    if "expiry" in df.columns:
        df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
    df["option_type"] = df.get("option_type", "put").astype(str).str.lower()
    df["lado"] = df.apply(lado, axis=1)
    df["contrato"] = np.where(df["option_type"].str.contains("call"), "C", "P")
    df["estilo"] = df.apply(estilo, axis=1)
    sg = []
    for _, r in df.iterrows():
        if r["lado"] == "COMPRA" and r["contrato"] == "C":
            sg.append(1)
        elif r["lado"] == "COMPRA" and r["contrato"] == "P":
            sg.append(-1)
        elif r["lado"] == "VENTA" and r["contrato"] == "C":
            sg.append(-1)
        elif r["lado"] == "VENTA" and r["contrato"] == "P":
            sg.append(1)
        else:
            sg.append(0)
    df["signo"] = sg
    df["qdelta"] = df["signo"] * df["premium"]
    df["origen"] = ticker
    return df.dropna(subset=["hora"])[df["premium"] > 0]

def tape(fecha, ticker):
    out = []
    for h1, m1, h2, m2 in BLOQUES:
        a = datetime(fecha.year, fecha.month, fecha.day, h1, m1, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
        b = datetime(fecha.year, fecha.month, fecha.day, h2, m2, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
        data = get_json("https://api.unusualwhales.com/api/option-trades", {
            "ticker_symbol": ticker,
            "newer_than": a.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "older_than": b.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "min_premium": MIN_PREMIUM,
            "limit": LIMIT,
        })
        data = data if isinstance(data, list) else []
        print(f"  {ticker} {h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}: {len(data)}")
        out.extend(data)
        time.sleep(0.12)
    if not out:
        return pd.DataFrame()
    df = procesar_df(out, ticker).drop_duplicates(["hora", "premium"]).sort_values("hora")
    a, b = sesion(fecha)
    df = df[(df["hora"] >= a) & (df["hora"] <= b)]
    if "expiry" in df.columns:
        lim = fecha + timedelta(days=MAX_DTE)
        mask = df["expiry"].isna() | (df["expiry"] <= lim)
        if mask.any():
            df = df[mask]
    if SOLO_0DTE and "expiry" in df.columns:
        df = df[df["expiry"].isin([fecha, fecha + timedelta(days=1)])]
    return df

def precio(grupo, fecha):
    a, b = sesion(fecha)
    frames = []
    for iv in ("1m", "5m"):
        px = yf.download(YAHOO[grupo], period="10d", interval=iv, progress=False, auto_adjust=True)
        if px is None or px.empty:
            continue
        if isinstance(px.columns, pd.MultiIndex):
            px.columns = px.columns.get_level_values(0)
        px.index = pd.to_datetime(px.index)
        px.index = px.index.tz_localize("America/New_York") if px.index.tz is None else px.index.tz_convert(TZ)
        px = px[(px.index >= a - pd.Timedelta(minutes=5)) & (px.index <= b)]
        cols = [c for c in ("Open","High","Low","Close") if c in px.columns]
        if cols:
            frames.append(px[cols].copy())
    if not frames:
        return pd.DataFrame()
    px = frames[0]
    if len(frames) > 1:
        extra = frames[1]
        miss = extra.index.difference(px.index)
        if len(miss):
            px = pd.concat([px, extra.loc[miss]]).sort_index()
    return px[~px.index.duplicated()].sort_index()

def qdelta_30m(df):
    if df is None or df.empty:
        return 0.0
    t1 = df["hora"].max()
    return float(df.loc[df["hora"] >= t1 - pd.Timedelta(minutes=30), "qdelta"].sum())

def semaforo(last, niv, ratio, qd30, vol, hi, lo):
    qf, pw, cw = niv.get("QF"), niv.get("PW"), niv.get("CW")
    p1 = 0
    if qf:
        p1 = 1 if last >= qf else -1
    if pw and last < pw:
        p1 = -1
    if cw and last > cw:
        p1 = 1
    p2 = 1 if qd30 > 0 else -1 if qd30 < 0 else 0
    if ratio >= 1.2 and p2 >= 0:
        p2 = 1
    if ratio <= 0.8 and p2 <= 0:
        p2 = -1
    rng = (hi - lo) / max(abs(last), 1)
    imp = vol.get("imp_move_pct") or 0
    ivp = vol.get("ivp") or 50
    p3 = p2 if imp and rng < imp * 0.7 else 0
    if ivp and ivp >= 80:
        p3 = 0
    up = sum(v > 0 for v in (p1, p2, p3))
    dn = sum(v < 0 for v in (p1, p2, p3))
    if up >= 2 and up > dn:
        return {"p1": p1, "p2": p2, "p3": p3, "color": "🟢", "texto": f"{up}/3 ALCISTA"}
    if dn >= 2 and dn > up:
        return {"p1": p1, "p2": p2, "p3": p3, "color": "🔴", "texto": f"{dn}/3 BAJISTA"}
    return {"p1": p1, "p2": p2, "p3": p3, "color": "🟡", "texto": f"{max(up, dn)}/3 NEUTRO"}

def pintar_print(ax, r, y, fecha, etiqueta=True):
    gold, col = "#d4af37", ("#2ecc71" if r["signo"] > 0 else "#e74c3c")
    ax.scatter(r["hora"], y, s=200, facecolors="none", edgecolors=gold, lw=1.5, zorder=6)
    ax.scatter(r["hora"], y, s=26, c=gold, zorder=7)
    ax.scatter(r["hora"], y, s=34, c=col, marker="^" if r["signo"] > 0 else "v", zorder=8)
    if etiqueta:
        txt = f"{fmt_usd(r['premium'])} {r['contrato']}{dte_de(r, fecha)}"
        if r["estilo"] != "PRT":
            txt += f" {r['estilo']}"
        ax.annotate(txt, (r["hora"], y), textcoords="offset points", xytext=(5, 8),
                    color=gold, fontsize=7.2, fontweight="bold")

def grafico(grupo, df, etiqueta, fecha, niv, vol, net, extra):
    px = precio(grupo, fecha)
    if px.empty:
        print("  sin precio", grupo)
        return None
    a0, b0 = sesion(fecha)
    bg, fg, grid, gold = "#0b1220", "#e8eef7", "#1d2a3d", "#d4af37"
    last = float(px["Close"].iloc[-1])
    open_px = float(px["Open"].iloc[0]) if "Open" in px.columns else float(px["Close"].iloc[0])
    lo_s = float(px["Low"].min()) if "Low" in px.columns else float(px["Close"].min())
    hi_s = float(px["High"].max()) if "High" in px.columns else float(px["Close"].max())
    t_low = px["Low"].idxmin() if "Low" in px.columns else px["Close"].idxmin()
    umbral = UMBRAL_BURBUJA if UMBRAL_BURBUJA > 0 else MIN_BURBUJA.get(grupo, 1_500_000)

    fig = plt.figure(figsize=(FIG_ANCHO, FIG_ALTO), facecolor=bg)
    gs = fig.add_gridspec(4, 1, hspace=0.06,
                          height_ratios=[PRECIO_ALTO, FRANJA_ALTO, QD_ALTO, TOTAL_ALTO])
    ax1 = fig.add_subplot(gs[0])
    axA = fig.add_subplot(gs[1], sharex=ax1)
    axQ = fig.add_subplot(gs[2], sharex=ax1)
    axT = fig.add_subplot(gs[3], sharex=ax1)
    for ax in (ax1, axA, axQ, axT):
        ax.set_facecolor(bg)
        ax.tick_params(colors=fg, labelsize=8)
        ax.grid(True, color=grid, alpha=0.28)
        for s in ax.spines.values():
            s.set_color(grid)
        ax.set_xlim(a0, b0)
    axA.grid(False)
    plt.setp(ax1.get_xticklabels(), visible=False)
    plt.setp(axA.get_xticklabels(), visible=False)
    plt.setp(axQ.get_xticklabels(), visible=False)

    ax1.axvspan(a0, t_low, color="#e74c3c", alpha=0.07, zorder=0)
    ax1.axvspan(t_low, b0, color="#2ecc71", alpha=0.06, zorder=0)
    ax1.plot(px.index, px["Close"], color="#6ea8ff", lw=1.55, zorder=3)
    ax1.axhline(open_px, color="#8b9bb0", lw=0.8, ls=":", alpha=0.85)
    ax1.axvline(t_low, color="#f1c40f", lw=0.8, ls="--", alpha=0.7)
    pad = (hi_s - lo_s) * 0.10 or last * 0.002
    ax1.set_ylim(lo_s - pad, hi_s + pad)
    ax1.text(a0, open_px, f" OPEN {open_px:.2f}", color="#8b9bb0", fontsize=7, va="bottom")
    ax1.text(t_low, lo_s, " LOW", color="#f1c40f", fontsize=7, va="top")

    caida = rebote = pd.DataFrame()
    n_c = n_r = 0.0
    if df is not None and not df.empty:
        caida = df[df["hora"] <= t_low]
        rebote = df[df["hora"] > t_low]
        n_c = float(caida["qdelta"].sum()) if not caida.empty else 0
        n_r = float(rebote["qdelta"].sum()) if not rebote.empty else 0
        ax1.text(a0, hi_s, f" CAÍDA {fmt_usd(n_c)}", color="#e74c3c", fontsize=8, fontweight="bold", va="top")
        ax1.text(t_low, hi_s, f"  REBOTE {fmt_usd(n_r)}", color="#2ecc71", fontsize=8, fontweight="bold", va="top")

        grandes = df[df["premium"] >= umbral]
        tops = []
        if not caida.empty:
            tops.append(caida.nlargest(3, "premium"))
        if not rebote.empty:
            tops.append(rebote.nlargest(3, "premium"))
        if not grandes.empty:
            tops.append(grandes)
        lab = pd.concat(tops).drop_duplicates(["hora", "premium"]) if tops else grandes
        ya = set()
        for _, r in lab.iterrows():
            y = r["spot"] if r["spot"] else last
            if not (lo_s - pad <= y <= hi_s + pad):
                y = last
            key = (r["hora"], r["premium"])
            pintar_print(ax1, r, y, fecha, etiqueta=key not in ya and len(ya) < MAX_ETIQUETAS)
            ya.add(key)

        # TOTAL: todos los prints (gris) + grandes (oro)
        axT.bar(df["hora"], df["premium"] / 1e6, width=0.0022, color="#6b7280", alpha=0.45)
        if not grandes.empty:
            axT.bar(grandes["hora"], grandes["premium"] / 1e6, width=0.003, color=gold, alpha=0.95)
        for _, r in lab.nlargest(5, "premium").iterrows():
            axT.annotate(fmt_usd(r["premium"]), (r["hora"], r["premium"] / 1e6),
                         textcoords="offset points", xytext=(0, 4), ha="center", color=gold, fontsize=7)

        qd = df.set_index("hora")["qdelta"].resample("5min").sum().fillna(0)
        axQ.bar(qd.index, qd.values / 1e6, width=0.0026,
                color=["#2ecc71" if v >= 0 else "#e74c3c" for v in qd.values])

    for k, col in (("PW", "#e74c3c"), ("QF", "#1aa3a3"), ("CW", "#2ecc71")):
        v = niv.get(k)
        if v is None or v < lo_s - pad * 2 or v > hi_s + pad * 2:
            continue
        ax1.axhline(v, color=col, ls="--", lw=1.15)
        ax1.text(a0, v, f" {k} {v:.2f} ", color="white", fontsize=8,
                 fontweight="bold", va="bottom", bbox=dict(fc=col, ec="none", pad=0.2))
    ax1.text(1.0, last, f" {last:,.2f} ", transform=ax1.get_yaxis_transform(),
             color="white", fontsize=8, va="center", ha="left",
             bbox=dict(fc="#3d5afe", ec="none", pad=0.22))
    ax1.set_ylabel("PRECIO", color=fg, fontsize=8)

    serie = net.get("serie", pd.Series(dtype=float))
    axA.set_ylim(0, 1); axA.set_yticks([]); axA.set_ylabel("AGRESOR", color=fg, fontsize=7)
    if len(serie):
        vals = serie.values.astype(float)
        mx = np.percentile(np.abs(vals), 85) or 1
        paso = pd.Timedelta(minutes=max(int(FRANJA_MIN), 3))
        for ts, v in zip(serie.index, vals):
            n = max(min(v / mx, 1), -1)
            c = "#d4af37" if n >= 0.25 else "#5ec8c6" if n >= 0 else "#8b6cc9" if n <= -0.25 else "#7aa0c4"
            axA.axvspan(ts, ts + paso, ymin=0.08, ymax=0.92, color=c, lw=0, alpha=0.88)

    axQ.axhline(0, color=fg, lw=0.5)
    axQ.set_ylabel("QD NOTIONAL $M", color=fg, fontsize=7)
    axT.set_ylabel("TOTAL $M", color=fg, fontsize=7)
    axT.xaxis.set_major_locator(mdates.HourLocator(interval=1, tz=TZ))
    axT.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ))

    ratio = float(net.get("flow_ratio", 1) or 1)
    sem = extra.get("sem", {"texto": "—", "color": "🟡"})
    qd30 = extra.get("qd30", 0)
    iv = f" IV {vol['iv']:.1f}%" if vol.get("iv") else ""
    ax1.set_title(
        f"{grupo} {etiqueta} {fecha}  |  {sem['color']} {sem['texto']}  |  "
        f"caída {fmt_usd(n_c)}  rebote {fmt_usd(n_r)}  30m {fmt_usd(qd30)}  flow {ratio:.2f}{iv}",
        color=fg, loc="left", fontsize=9.5, pad=6,
    )
    fig.text(0.01, 0.008,
             f"NY {datetime.now(TZ):%H:%M}  COL {datetime.now(TZ_COL):%H:%M}  "
             f"rojo=OPEN→LOW  verde=LOW→cierre  |  gris=prints chicos  oro=grandes  |  print ≠ dirección ETF",
             color="#8b9bb0", fontsize=8)
    fig.tight_layout(rect=[0, 0.025, 1, 1])
    ruta = os.path.join(CARPETA, f"{grupo}_{etiqueta}_{fecha}_{datetime.now(TZ):%H%M%S}.png")
    fig.savefig(ruta, dpi=DPI, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    print("  PNG", ruta)
    return {
        "ticker": grupo, "modo": etiqueta, "fecha": str(fecha),
        "semaforo": sem["texto"], "color": sem["color"],
        "p1": sem["p1"], "p2": sem["p2"], "p3": sem["p3"],
        "qdelta": n_c + n_r, "qd30": qd30, "flow_ratio": ratio,
        "iv": vol.get("iv"), "ivp": vol.get("ivp"), "imp_move_pct": vol.get("imp_move_pct"),
        "cw": niv.get("CW"), "pw": niv.get("PW"), "qf": niv.get("QF"),
        "caida": n_c, "rebote": n_r,
    }

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
        if not df.empty and spot:
            df.loc[df["spot"] <= 0, "spot"] = spot
        lo = float(px["Low"].min()) if not px.empty and "Low" in px.columns else (spot or 0)
        hi = float(px["High"].max()) if not px.empty and "High" in px.columns else (spot or 0)
        niv = {}
        for tk in ticks:
            for k, v in niveles(tk, fecha, spot).items():
                if v is not None and k not in niv:
                    niv[k] = v
        vol = vol_extra(ticks[0], fecha)
        net = net_prem(ticks[0], fecha)
        if grupo == "SPX" and "SPXW" in ticks:
            n2 = net_prem("SPXW", fecha)
            net["net_call"] += n2["net_call"]
            net["net_put"] += n2["net_put"]
            if len(n2["serie"]):
                net["serie"] = net["serie"].add(n2["serie"], fill_value=0)
        qd30 = qdelta_30m(df)
        sem = semaforo(spot or 0, niv, net["flow_ratio"], qd30, vol, hi, lo)
        card = grafico(grupo, df, etiqueta, fecha, niv, vol, net, {"qd30": qd30, "sem": sem})
        if card:
            resumen.append(card)
        if df is not None and not df.empty:
            for _, r in df.nlargest(3, "premium").iterrows():
                if r["premium"] >= ALERTA_USD:
                    telegram(f"{grupo} {sem['texto']} {fmt_usd(r['premium'])} {r['contrato']}")

def main():
    if not API_KEY:
        print("Falta UW_API_KEY")
        return
    modo = os.getenv("MODO_FLUJO", "AMBOS").upper()
    resumen = []
    if modo in ("AYER", "AMBOS"):
        procesar(ayer(), "AYER", resumen)
    if modo in ("HOY", "AMBOS"):
        procesar(datetime.now(TZ).date(), "HOY", resumen)
    with open(os.path.join(CARPETA, "resumen.json"), "w") as f:
        json.dump(resumen, f, default=str)

if __name__ == "__main__":
    main()
