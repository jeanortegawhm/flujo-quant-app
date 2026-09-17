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
FIG_ALTO = float(os.getenv("FIG_ALTO", "14.0"))
FRANJA_ALTO = float(os.getenv("FRANJA_ALTO", "0.45"))
FRANJA_MIN = float(os.getenv("FRANJA_MIN", "1"))
PRECIO_ALTO = float(os.getenv("PRECIO_ALTO", "3.2"))
TOTAL_ALTO = float(os.getenv("TOTAL_ALTO", "1.05"))
QDELTA_ALTO = float(os.getenv("QDELTA_ALTO", "1.05"))
GEX_ALTO = float(os.getenv("GEX_ALTO", "1.20"))
DPI = int(os.getenv("DPI_FIG", "118"))
MAX_DTE = int(os.getenv("MAX_DTE", "7"))

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

def gex_por_strike(tk, fecha, spot):
    params = {"date": str(fecha)}
    if spot:
        params["min_strike"] = round(spot * 0.97, 2)
        params["max_strike"] = round(spot * 1.03, 2)
    urls = [
        f"https://api.unusualwhales.com/api/stock/{tk}/spot-exposures/strike",
        f"https://api.unusualwhales.com/api/stock/{tk}/greek-exposure/strike",
        f"https://api.unusualwhales.com/api/stock/{tk}/flow-per-strike",
    ]
    rows, usado = None, ""
    for url in urls:
        data = get_json(url, params)
        if isinstance(data, list) and data:
            rows, usado = data, url.split("/")[-1]
            break
    if not rows:
        print("  GEX strike vacío", tk)
        return pd.DataFrame()
    print("  GEX strike", tk, usado, len(rows))
    df = pd.DataFrame(rows)
    col_k = next((c for c in ("strike", "strike_price", "k") if c in df.columns), None)
    if not col_k:
        return pd.DataFrame()
    df["strike"] = pd.to_numeric(df[col_k], errors="coerce")
    cgi = pd.to_numeric(df.get("call_gamma_oi", df.get("call_gex", 0)), errors="coerce").fillna(0)
    pgi = pd.to_numeric(df.get("put_gamma_oi", df.get("put_gex", 0)), errors="coerce").fillna(0)
    if (cgi.abs() + pgi.abs()).sum() > 0:
        df["gex"] = cgi + pgi
    elif "call_premium" in df.columns:
        df["gex"] = pd.to_numeric(df["call_premium"], errors="coerce").fillna(0) - \
                    pd.to_numeric(df.get("put_premium", 0), errors="coerce").fillna(0)
    else:
        gcol = next((c for c in ("gex", "gamma", "net_gex") if c in df.columns), None)
        if not gcol:
            return pd.DataFrame()
        df["gex"] = pd.to_numeric(df[gcol], errors="coerce").fillna(0)
    df = df.dropna(subset=["strike"])
    if spot:
        df = df[abs(df["strike"] - spot) / max(abs(spot), 1) <= 0.03]
    return df.sort_values("strike")

def vol_extra(tk, fecha):
    """IV 30d, percentil, implied move 1d."""
    out = {"iv": None, "ivp": None, "ivr": None, "imp_move": None, "imp_move_pct": None}
    rows = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/interpolated-iv", {"date": str(fecha)})
    if isinstance(rows, list) and rows:
        d1 = next((r for r in rows if num(r.get("days")) == 1), None)
        d30 = next((r for r in rows if num(r.get("days")) == 30), rows[-1] if rows else None)
        if d1:
            out["imp_move_pct"] = num(d1.get("implied_move_perc"))
        if d30:
            out["iv"] = num(d30.get("volatility"))
            out["ivp"] = num(d30.get("percentile"))
    rk = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/iv-rank", {"date": str(fecha)})
    if isinstance(rk, dict):
        out["ivr"] = num(rk.get("iv_rank") or rk.get("rank"))
    elif isinstance(rk, list) and rk:
        last = rk[-1] if isinstance(rk[-1], dict) else {}
        out["ivr"] = num(last.get("iv_rank") or last.get("rank"))
    st = get_json(f"https://api.unusualwhales.com/api/stock/{tk}/volatility/stats", {"date": str(fecha)})
    if isinstance(st, dict):
        if out["iv"] is None:
            out["iv"] = num(st.get("iv") or st.get("iv30"))
        if out["ivr"] is None:
            out["ivr"] = num(st.get("iv_rank"))
        if out["ivp"] is None:
            out["ivp"] = num(st.get("iv_percentile") or st.get("percentile"))
    if out["iv"] and out["iv"] < 3:
        out["iv"] *= 100
    return out

def qdelta_30m(df):
    if df is None or df.empty:
        return 0.0
    t1 = df["hora"].max()
    t0 = t1 - pd.Timedelta(minutes=30)
    return float(df.loc[df["hora"] >= t0, "qdelta"].sum())

def semaforo(last, niv, ratio, qd30, vol, hi, lo):
    """3 pilares. Señal solo con 2/3 o 3/3 al mismo lado."""
    qf, pw, cw = niv.get("QF"), niv.get("PW"), niv.get("CW")
    p1 = 0
    if qf:
        p1 = 1 if last >= qf else -1
    if pw and last < pw:
        p1 = -1
    if cw and last > cw:
        p1 = 1
    p2 = 0
    if qd30 > 0 and ratio >= 1.15:
        p2 = 1
    elif qd30 < 0 and ratio <= 0.85:
        p2 = -1
    elif qd30 > 0:
        p2 = 1
    elif qd30 < 0:
        p2 = -1
    rng = (hi - lo) / max(abs(last), 1)
    imp = vol.get("imp_move_pct") or 0
    ivp = vol.get("ivp") or vol.get("ivr") or 50
    p3 = 0
    if imp and rng < imp * 0.7:
        p3 = p2 if p2 else p1
    elif imp and rng > imp:
        p3 = 0
    if ivp and ivp >= 80:
        p3 = 0
    votos = [p1, p2, p3]
    n_up = sum(1 for v in votos if v > 0)
    n_dn = sum(1 for v in votos if v < 0)
    if n_up >= 2 and n_up > n_dn:
        color, txt = "🟢", f"{n_up}/3 ALCISTA"
    elif n_dn >= 2 and n_dn > n_up:
        color, txt = "🔴", f"{n_dn}/3 BAJISTA"
    else:
        color, txt = "🟡", f"{max(n_up, n_dn)}/3 NEUTRO"
    return {
        "p1": p1, "p2": p2, "p3": p3,
        "n_up": n_up, "n_dn": n_dn,
        "color": color, "texto": txt,
    }

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
    serie = df.dropna(subset=["hora"]).set_index("hora")["agres"].resample("1min").sum().fillna(0)
    return {"net_call": tc, "net_put": tp, "flow_ratio": ratio, "serie": serie}

def lado(row):
    tags = str(row.get("tags", "") or "").lower()
    if "ask_side" in tags:
        return "COMPRA"
    if "bid_side" in tags:
        return "VENTA"
    p = pd.to_numeric(row.get("price"), errors="coerce")
    b = pd.to_numeric(row.get("nbbo_bid"), errors="coerce")
    a = pd.to_numeric(row.get("nbbo_ask"), errors="coerce")
    if pd.notna(p) and pd.notna(b) and pd.notna(a) and a > b:
        return "COMPRA" if p >= (b + a) / 2 else "VENTA"
    return "INDEF"

def recalcular_expo(df, spot=None):
    if df is None or df.empty:
        return df
    df = df.copy()
    if spot:
        df.loc[df["spot"] <= 0, "spot"] = spot
    expo = df["delta"].abs() * df["size"] * 100 * df["spot"].clip(lower=0)
    df["exposicion"] = np.where(expo > 0, expo, df["premium"].clip(lower=0))
    df["qdelta"] = df["signo"] * df["delta"].abs() * df["size"] * 100
    return df

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
    df["delta"] = pd.to_numeric(df.get("delta", 0), errors="coerce").fillna(0)
    df["size"] = pd.to_numeric(df.get("size", 0), errors="coerce").fillna(0)
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
    df["origen"] = ticker
    df = df.dropna(subset=["hora"])
    df = df[df["premium"] > 0]
    return recalcular_expo(df)

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
    df = procesar_df(out, ticker).drop_duplicates(["hora", "premium", "size"]).sort_values("hora")
    if "expiry" in df.columns:
        lim = fecha + timedelta(days=MAX_DTE)
        mask = df["expiry"].isna() | (df["expiry"] <= lim)
        if mask.any():
            df = df[mask]
    if SOLO_0DTE and "expiry" in df.columns:
        df = df[df["expiry"].isin([fecha, fecha + timedelta(days=1)])]
    return df

def precio(grupo, fecha):
    px = yf.download(YAHOO[grupo], period="7d", interval="1m", progress=False, auto_adjust=True)
    if px.empty:
        px = yf.download(YAHOO[grupo], period="7d", interval="5m", progress=False, auto_adjust=True)
    if px.empty:
        return px
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    px.index = pd.to_datetime(px.index)
    px.index = px.index.tz_localize("America/New_York") if px.index.tz is None else px.index.tz_convert(TZ)
    return px[px.index.date == fecha].between_time("09:30", "16:00")

def grafico(grupo, df, etiqueta, fecha, niv, vol, net, gex_df, extra):
    px = precio(grupo, fecha)
    if px.empty:
        print("  sin precio", grupo)
        return None
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
    pad = (hi - lo) * 0.06 or last * 0.002
    ax1.set_ylim(lo - pad, hi + pad)

    neto, grandes = 0.0, pd.DataFrame()
    if df is not None and not df.empty:
        grandes = df[df["exposicion"] >= umbral]
        if grandes.empty:
            grandes = df.nlargest(6, "exposicion")
        top = grandes.nlargest(MAX_ETIQUETAS, "exposicion")
        for _, r in grandes.iterrows():
            y = r["spot"] if r["spot"] else last
            if y < lo - pad or y > hi + pad:
                y = last
            col = "#2ecc71" if r["signo"] > 0 else "#e74c3c"
            ax1.scatter(r["hora"], y, s=200, facecolors="none", edgecolors=gold, lw=1.6, zorder=6)
            ax1.scatter(r["hora"], y, s=26, c=gold, zorder=7)
            ax1.scatter(r["hora"], y, s=34, c=col, marker="^" if r["signo"] > 0 else "v", zorder=8)
        for _, r in top.iterrows():
            y = r["spot"] if r["spot"] else last
            if y < lo - pad or y > hi + pad:
                y = last
            txt = f"{fmt_usd(r['exposicion'])} {r['contrato']}{dte_de(r, fecha)}"
            if r["estilo"] != "PRT":
                txt += f" {r['estilo']}"
            ax1.annotate(txt, (r["hora"], y), textcoords="offset points", xytext=(6, 9),
                         color=gold, fontsize=7.5, fontweight="bold")
        neto = float(df["qdelta"].sum())

    for k, col in (("PW", "#e74c3c"), ("QF", "#1aa3a3"), ("CW", "#2ecc71"), ("MAGNET", "#9b59b6")):
        v = niv.get(k)
        if v is None or not (lo - pad <= v <= hi + pad):
            continue
        ax1.axhline(v, color=col, ls="--", lw=1.1)
        ax1.text(px.index[0], v, f" {k} {v:.2f} ", color="white", fontsize=8,
                 fontweight="bold", va="bottom", bbox=dict(fc=col, ec="none", pad=0.2))
    ax1.text(1.0, last, f" {last:,.2f} ", transform=ax1.get_yaxis_transform(),
             color="white", fontsize=8, va="center", ha="left",
             bbox=dict(fc="#3d5afe", ec="none", pad=0.22))
    ax1.set_ylabel("PRECIO", color=fg, fontsize=8)

    serie = net.get("serie", pd.Series(dtype=float))
    axA.set_ylim(0, 1)
    axA.set_yticks([])
    axA.set_ylabel("AGRESOR", color=fg, fontsize=7)
    if len(serie):
        vals = serie.values.astype(float)
        mx = np.percentile(np.abs(vals), 90) or 1
        paso = pd.Timedelta(minutes=max(FRANJA_MIN, 1))
        for ts, v in zip(serie.index, vals):
            n = max(min(v / mx, 1), -1)
            c = "#d4af37" if n >= 0.15 else "#2aa3a1" if n >= 0 else "#7e57c2" if n <= -0.15 else "#5b7c99"
            axA.axvspan(ts, ts + paso, ymin=0, ymax=1, color=c, lw=0, alpha=0.95)

    if not grandes.empty:
        axT.bar(grandes["hora"], grandes["exposicion"] / 1e6, width=0.003, color=gold, alpha=0.92)
        for _, r in grandes.nlargest(4, "exposicion").iterrows():
            axT.annotate(fmt_usd(r["exposicion"]), (r["hora"], r["exposicion"] / 1e6),
                         textcoords="offset points", xytext=(0, 5), ha="center", color=gold, fontsize=7)
    axT.set_ylabel("TOTAL $M", color=fg, fontsize=7)

    if df is not None and not df.empty:
        qd = df.set_index("hora")["qdelta"].resample("2min").sum().fillna(0)
        axQ.bar(qd.index, qd.values / 1e6, width=0.0014,
                color=["#2ecc71" if v >= 0 else "#e74c3c" for v in qd.values])
    axQ.axhline(0, color=fg, lw=0.5)
    axQ.set_ylabel("QΔ $M", color=fg, fontsize=7)
    axQ.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ))

    if gex_df is not None and not gex_df.empty:
        cols = ["#2ecc71" if v >= 0 else "#e74c3c" for v in gex_df["gex"]]
        w = max((gex_df["strike"].max() - gex_df["strike"].min()) / 40, 0.2)
        axG.bar(gex_df["strike"], gex_df["gex"], color=cols, width=w)
        axG.axvline(last, color="#6ea8ff", ls="--", lw=1)
        axG.set_xlabel("Strike", color=fg, fontsize=8)
    else:
        axG.set_xlim(lo, hi)
        axG.set_xticks([])
        axG.text(0.5, 0.5, "Sin GEX/strike (plan o ticker)",
                 transform=axG.transAxes, ha="center", va="center",
                 color="#8b9bb0", fontsize=8)
    axG.set_ylabel("GEX strike", color=fg, fontsize=7)

    ratio = float(net.get("flow_ratio", 1) or 1)
    qd30 = extra.get("qd30", 0)
    sem = extra.get("sem", {"texto": "—", "color": "🟡"})
    iv = f" IV {vol['iv']:.1f}%" if vol.get("iv") else ""
    ivp = f" IVP {vol['ivp']:.0f}" if vol.get("ivp") is not None else ""
    im = f" IM {vol['imp_move_pct']*100:.2f}%" if vol.get("imp_move_pct") else ""
    ax1.set_title(
        f"{grupo} {etiqueta} {fecha}  |  {sem['color']} {sem['texto']}  |  "
        f"qΔ {fmt_usd(neto)}  30m {fmt_usd(qd30)}  flow {ratio:.2f}{iv}{ivp}{im}",
        color=fg, loc="left", fontsize=10, pad=6,
    )
    fig.text(0.01, 0.008,
             f"NY {datetime.now(TZ):%H:%M}  COL {datetime.now(TZ_COL):%H:%M}  "
             f"P1 GEX  P2 flujo 30m  P3 vol/IM  |  print ≠ dirección ETF",
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
        "qdelta": neto, "qd30": qd30, "flow_ratio": ratio,
        "iv": vol.get("iv"), "ivp": vol.get("ivp"), "ivr": vol.get("ivr"),
        "imp_move_pct": vol.get("imp_move_pct"),
        "cw": niv.get("CW"), "pw": niv.get("PW"), "qf": niv.get("QF"),
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
        df = recalcular_expo(df, spot)
        lo = float(px["Low"].min()) if not px.empty and "Low" in px.columns else (spot or 0)
        hi = float(px["High"].max()) if not px.empty and "High" in px.columns else (spot or 0)
        niv, gex = {}, pd.DataFrame()
        for tk in ticks:
            for k, v in niveles(tk, fecha, spot).items():
                if v is not None and k not in niv:
                    niv[k] = v
            if gex.empty:
                gex = gex_por_strike(tk, fecha, spot)
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
        extra = {"qd30": qd30, "sem": sem}
        card = grafico(grupo, df, etiqueta, fecha, niv, vol, net, gex, extra)
        if card:
            resumen.append(card)
        if df is not None and not df.empty:
            for _, r in df.nlargest(3, "exposicion").iterrows():
                if r["exposicion"] >= ALERTA_USD:
                    telegram(f"{grupo} {sem['texto']} {fmt_usd(r['exposicion'])} {r['contrato']} {r['estilo']}")

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