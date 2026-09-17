import os, json, time, warnings, requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import LinearSegmentedColormap
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")

API_KEY = os.getenv("UW_API_KEY", "")
GRUPOS = {
    "SPX": ["SPX", "SPXW"], "SPY": ["SPY"], "QQQ": ["QQQ"],
    "IWM": ["IWM"], "IBIT": ["IBIT"], "GLD": ["GLD"],
}
YAHOO = {"SPX": "^GSPC", "SPY": "SPY", "QQQ": "QQQ", "IWM": "IWM", "IBIT": "IBIT", "GLD": "GLD"}
MIN_BURBUJA = {
    "SPX": 150_000_000, "SPY": 80_000_000, "QQQ": 80_000_000,
    "IWM": 25_000_000, "IBIT": 5_000_000, "GLD": 15_000_000,
}
MIN_PREMIUM = int(os.getenv("MIN_PREMIUM", "120000"))
SOLO_0DTE = os.getenv("SOLO_0DTE", "0") == "1"
UMBRAL_BURBUJA = float(os.getenv("UMBRAL_BURBUJA", "0") or 0)
ALERTA_USD = float(os.getenv("ALERTA_USD", "2000000"))
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
LIMIT, MAX_ETIQUETAS = 200, 6
TZ_MERCADO, TZ_VER = ZoneInfo("America/New_York"), ZoneInfo("America/Bogota")
CARPETA = os.getenv("FLUJOS_DIR", os.path.join(os.path.expanduser("~"), "flujos"))
os.makedirs(CARPETA, exist_ok=True)
headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
BLOQUES = [(9, 25, 11, 0), (11, 0, 12, 30), (12, 30, 14, 0), (14, 0, 15, 15), (15, 15, 16, 15)]


def dia_habil_anterior():
    d = datetime.now(TZ_MERCADO).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def fmt_usd(x):
    x = float(x or 0)
    s = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1e9:
        return f"{s}${x/1e9:.2f}B"
    if x >= 1e6:
        return f"{s}${x/1e6:.1f}M"
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
            print("  API", r.status_code, url.split("/")[-1])
            return None
        p = r.json()
        return p.get("data") if isinstance(p, dict) else p
    except Exception as e:
        print("  api", e)
        return None


def telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg},
            timeout=12,
        )
    except Exception as e:
        print("  telegram", e)


def obtener_niveles(ticker, fecha, spot=None):
    raw = {}
    for source in ("oi", "vol"):
        data = get_json(
            f"https://api.unusualwhales.com/api/stock/{ticker}/gex-levels",
            {"date": str(fecha), "source": source},
        )
        if isinstance(data, dict):
            for k, campo in (
                ("CW", "call_wall"),
                ("PW", "put_wall"),
                ("QF", "gamma_flip"),
                ("MAGNET", "gamma_magnet"),
            ):
                v = num(data.get(campo))
                if v is None:
                    continue
                if spot and abs(v - spot) / max(abs(spot), 1) > 0.20:
                    continue
                raw[f"{k}_{source.upper()}"] = v
    out = {k: raw.get(f"{k}_OI") or raw.get(f"{k}_VOL") for k in ("CW", "PW", "QF", "MAGNET")}
    print("  Niveles", ticker, out)
    return out


def obtener_vol(ticker, fecha):
    out = {"iv": None, "ivr": None}
    d = get_json(
        f"https://api.unusualwhales.com/api/stock/{ticker}/volatility/stats",
        {"date": str(fecha)},
    )
    if isinstance(d, dict):
        out["iv"] = num(d.get("iv"))
        out["ivr"] = num(d.get("iv_rank"))
        if out["iv"] and out["iv"] < 5:
            out["iv"] *= 100
        if out["ivr"] and out["ivr"] < 3:
            out["ivr"] *= 100
    return out


def obtener_net_premium(ticker, fecha):
    vacio = {"net_call": 0, "net_put": 0, "flow_ratio": 1.0, "serie": pd.Series(dtype=float)}
    rows = get_json(
        f"https://api.unusualwhales.com/api/stock/{ticker}/net-prem-ticks",
        {"date": str(fecha)},
    )
    if not isinstance(rows, list) or not rows:
        return vacio
    df = pd.DataFrame(rows)
    df["hora"] = pd.to_datetime(df.get("tape_time"), utc=True, errors="coerce").dt.tz_convert(TZ_MERCADO)
    df["net_call"] = pd.to_numeric(df.get("net_call_premium", 0), errors="coerce").fillna(0)
    df["net_put"] = pd.to_numeric(df.get("net_put_premium", 0), errors="coerce").fillna(0)
    df["agres"] = df["net_call"] - df["net_put"]
    tc, tp = float(df["net_call"].sum()), float(df["net_put"].sum())
    bull = max(tc, 0) + max(-tp, 0)
    bear = max(-tc, 0) + max(tp, 0)
    ratio = bull / bear if bear > 0 else (2.0 if bull > 0 else 1.0)
    serie = df.dropna(subset=["hora"]).set_index("hora")["agres"].resample("1min").sum().fillna(0)
    return {"net_call": tc, "net_put": tp, "flow_ratio": ratio, "serie": serie}


def lado_trade(row):
    tags = str(row.get("tags", "") or "").lower()
    if any(x in tags for x in ("ask_side", "sweep", "ask")) and "bid_side" not in tags:
        return "COMPRA"
    if "bid_side" in tags or "bid" in tags:
        return "VENTA"
    price = pd.to_numeric(row.get("price"), errors="coerce")
    bid = pd.to_numeric(row.get("nbbo_bid"), errors="coerce")
    ask = pd.to_numeric(row.get("nbbo_ask"), errors="coerce")
    if pd.notna(price) and pd.notna(bid) and pd.notna(ask) and ask > bid:
        mid = (bid + ask) / 2
        if price >= mid + 0.1 * (ask - bid):
            return "COMPRA"
        if price <= mid - 0.1 * (ask - bid):
            return "VENTA"
    return "INDEF"


def procesar_df(data, ticker):
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["origen"] = ticker
    raw = df["executed_at"] if "executed_at" in df.columns else df.get("created_at")
    try:
        s0 = str(raw.iloc[0]) if raw is not None and len(raw) else ""
        if s0.replace(".", "", 1).isdigit():
            df["hora"] = pd.to_datetime(pd.to_numeric(raw, errors="coerce"), unit="ms", utc=True, errors="coerce")
        else:
            df["hora"] = pd.to_datetime(raw, utc=True, errors="coerce")
    except Exception:
        df["hora"] = pd.to_datetime(raw, utc=True, errors="coerce")
    df["hora"] = df["hora"].dt.tz_convert(TZ_MERCADO)
    df["premium"] = pd.to_numeric(df.get("premium", 0), errors="coerce").fillna(0)
    df["delta"] = pd.to_numeric(df.get("delta", 0), errors="coerce").fillna(0)
    df["size"] = pd.to_numeric(df.get("size", 0), errors="coerce").fillna(0)
    df["spot"] = pd.to_numeric(df.get("underlying_price", 0), errors="coerce").fillna(0)
    if "expiry" in df.columns:
        df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
    df["option_type"] = df.get("option_type", "put").astype(str).str.lower()
    df["lado"] = df.apply(lado_trade, axis=1)
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
    df = df.dropna(subset=["hora"])
    df = df[df["premium"] > 0]
    return df


def obtener_tape(fecha, ticker):
    partes = []
    url = "https://api.unusualwhales.com/api/option-trades"
    for h1, m1, h2, m2 in BLOQUES:
        ini = datetime(fecha.year, fecha.month, fecha.day, h1, m1, tzinfo=TZ_MERCADO).astimezone(ZoneInfo("UTC"))
        fin = datetime(fecha.year, fecha.month, fecha.day, h2, m2, tzinfo=TZ_MERCADO).astimezone(ZoneInfo("UTC"))
        data = get_json(url, {
            "ticker_symbol": ticker,
            "newer_than": ini.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "older_than": fin.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "min_premium": MIN_PREMIUM,
            "limit": LIMIT,
        })
        data = data if isinstance(data, list) else []
        print(f"  {ticker} {h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}: {len(data)}")
        partes.extend(data)
        time.sleep(0.12)
    if not partes:
        return pd.DataFrame()
    df = procesar_df(partes, ticker).drop_duplicates(subset=["hora", "premium", "size"]).sort_values("hora")
    if SOLO_0DTE and not df.empty and "expiry" in df.columns:
        man = fecha + timedelta(days=1)
        df = df[df["expiry"].isin([fecha, man])]
    return df


def cargar_precio(grupo, fecha):
    px = yf.download(YAHOO.get(grupo, grupo), period="7d", interval="1m", progress=False, auto_adjust=True)
    if px.empty:
        px = yf.download(YAHOO.get(grupo, grupo), period="7d", interval="5m", progress=False, auto_adjust=True)
    if px.empty:
        return px
    if isinstance(px.columns, pd.MultiIndex):
        px.columns = px.columns.get_level_values(0)
    px.index = pd.to_datetime(px.index)
    px.index = px.index.tz_localize("America/New_York") if px.index.tz is None else px.index.tz_convert(TZ_MERCADO)
    return px[px.index.date == fecha].between_time("09:30", "16:00")


def grafico(grupo, df, etiqueta, fecha, niveles, vol, net_prem):
    px = cargar_precio(grupo, fecha)
    if px.empty:
        print("  sin precio", grupo)
        return None
    bg, fg, grid = "#0b1220", "#e8eef7", "#1d2a3d"
    gold = "#d4af37"
    last = float(px["Close"].iloc[-1])
    umbral = MIN_BURBUJA.get(grupo, 30_000_000)
    if UMBRAL_BURBUJA > 0:
        umbral = UMBRAL_BURBUJA

    fig, axs = plt.subplots(
        4, 1, figsize=(12.2, 11.2), sharex=True,
        gridspec_kw={"height_ratios": [3.3, 0.28, 1.15, 1.15], "hspace": 0.06},
        facecolor=bg,
    )
    ax1, axA, axT, axQ = axs
    for ax in axs:
        ax.set_facecolor(bg)
        ax.tick_params(colors=fg, labelsize=8)
        ax.grid(True, color=grid, alpha=0.35)
        for s in ax.spines.values():
            s.set_color(grid)
    axA.grid(False)

    ax1.plot(px.index, px["Close"], color="#6ea8ff", lw=1.55)
    ax1.fill_between(px.index, px["Close"], px["Close"].min(), color="#6ea8ff", alpha=0.07)
    lo = float(px["Low"].min()) if "Low" in px.columns else float(px["Close"].min())
    hi = float(px["High"].max()) if "High" in px.columns else float(px["Close"].max())
    extras = [v for v in niveles.values() if v]
    if extras:
        lo, hi = min(lo, min(extras)), max(hi, max(extras))
    pad = (hi - lo) * 0.10 or 1
    ax1.set_ylim(lo - pad, hi + pad)

    neto = 0.0
    grandes = pd.DataFrame()
    if df is not None and not df.empty:
        grandes = df[df["exposicion"] >= umbral].copy()
        if grandes.empty:
            grandes = df.nlargest(8, "exposicion")
        top = grandes.nlargest(MAX_ETIQUETAS, "exposicion")
        for _, r in grandes.iterrows():
            y = r["spot"] if r["spot"] else last
            col = "#2ecc71" if r["signo"] > 0 else "#e74c3c"
            ax1.scatter(r["hora"], y, s=220, facecolors="none", edgecolors=gold, lw=1.6, zorder=6)
            ax1.scatter(r["hora"], y, s=28, c=gold, zorder=7)
            ax1.scatter(r["hora"], y, s=36, c=col, marker="^" if r["signo"] > 0 else "v", zorder=8)
        for _, r in top.iterrows():
            y = r["spot"] if r["spot"] else last
            ax1.annotate(fmt_usd(r["exposicion"]), (r["hora"], y),
                         textcoords="offset points", xytext=(6, 10),
                         color=gold, fontsize=8, fontweight="bold")
        neto = float(df["qdelta"].sum())

    for k, col in (("PW", "#e74c3c"), ("QF", "#1aa3a3"), ("CW", "#2ecc71"), ("MAGNET", "#9b59b6")):
        if niveles.get(k):
            ax1.axhline(niveles[k], color=col, ls="--", lw=1.1)
            ax1.text(px.index[0], niveles[k], f" {k} {niveles[k]:.2f} ",
                     color="white", fontsize=8, fontweight="bold", va="bottom",
                     bbox=dict(fc=col, ec="none", pad=0.22))
    ax1.text(1.0, last, f" {last:,.2f} ", transform=ax1.get_yaxis_transform(),
             color="white", fontsize=8, va="center", ha="left",
             bbox=dict(fc="#3d5afe", ec="none", pad=0.25))

    serie = net_prem.get("serie", pd.Series(dtype=float))
    if len(serie):
        cmap = LinearSegmentedColormap.from_list("ag", ["#7e57c2", "#90caf9", "#d4af37"])
        vals = serie.values.astype(float)
        mx = np.max(np.abs(vals)) or 1
        colors = cmap(np.clip((vals / mx + 1) / 2, 0, 1))
        axA.bar(serie.index, np.ones(len(serie)), width=0.00075, color=colors, alpha=0.95)
    axA.set_yticks([])
    axA.set_ylabel("AGRESOR", color=fg, fontsize=7)

    if not grandes.empty:
        axT.bar(grandes["hora"], grandes["exposicion"] / 1e6, width=0.003,
                color=gold, alpha=0.9, zorder=3)
        for _, r in grandes.nlargest(4, "exposicion").iterrows():
            axT.annotate(fmt_usd(r["exposicion"]), (r["hora"], r["exposicion"] / 1e6),
                         textcoords="offset points", xytext=(0, 6), ha="center",
                         color=gold, fontsize=7)
    axT.set_ylabel("TOTAL $M", color=fg, fontsize=7)

    if df is not None and not df.empty:
        qd = df.set_index("hora")["qdelta"].resample("2min").sum().fillna(0)
        axQ.bar(qd.index, qd.values / 1e6, width=0.0012,
                color=["#2ecc71" if v >= 0 else "#e74c3c" for v in qd.values], alpha=0.9)
    axQ.axhline(0, color=fg, lw=0.5)
    axQ.set_ylabel("Q Δ $M", color=fg, fontsize=7)
    axQ.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ_MERCADO))

    ratio = float(net_prem.get("flow_ratio", 1) or 1)
    qf, last_cmp = niveles.get("QF"), last
    if qf and last_cmp >= qf:
        regimen = "GEX+"
    elif qf and last_cmp < qf:
        regimen = "GEX-"
    else:
        regimen = "GEX?"
    if ratio >= 1.4:
        sesgo = "ALCISTA"
    elif ratio <= 0.7:
        sesgo = "BAJISTA"
    else:
        sesgo = "NEUTRO"
    iv = f"  IV {vol['iv']:.1f}%" if vol.get("iv") else ""
    ax1.set_title(
        f"{grupo}  {etiqueta}  {fecha}   |   {regimen} {sesgo}   |   qΔ {fmt_usd(neto)}   flow {ratio:.2f}{iv}",
        color=fg, loc="left", fontsize=12, pad=8,
    )
    fig.text(0.01, 0.01,
             f"NY {datetime.now(TZ_MERCADO):%H:%M}   COL {datetime.now(TZ_VER):%H:%M}   "
             f"print ≠ dirección ETF   |   OI+VOL GEX",
             color="#8b9bb0", fontsize=8)
    fig.tight_layout(rect=[0, 0.025, 1, 1])
    ruta = os.path.join(CARPETA, f"{grupo}_{etiqueta}_{fecha}_{datetime.now(TZ_MERCADO):%H%M%S}.png")
    fig.savefig(ruta, dpi=118, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    print("  PNG", ruta)
    return {
        "ticker": grupo, "fecha": str(fecha), "modo": etiqueta, "last": last,
        "regimen": regimen, "sesgo": sesgo, "qdelta": neto,
        "flow_ratio": ratio, "cw": niveles.get("CW"), "pw": niveles.get("PW"),
        "qf": niveles.get("QF"), "magnet": niveles.get("MAGNET"),
        "net_call": net_prem.get("net_call", 0), "net_put": net_prem.get("net_put", 0),
        "png": os.path.basename(ruta),
    }


def procesar(fecha, etiqueta, resumen):
    print("====", etiqueta, fecha)
    for grupo, ticks in GRUPOS.items():
        frames = []
        for tk in ticks:
            t = obtener_tape(fecha, tk)
            if t is not None and not t.empty:
                frames.append(t)
            time.sleep(0.08)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        px = cargar_precio(grupo, fecha)
        spot = float(px["Close"].iloc[-1]) if not px.empty else None
        niv = {}
        for tk in ticks:
            for k, v in obtener_niveles(tk, fecha, spot).items():
                if v is not None and k not in niv:
                    niv[k] = v
        vol = obtener_vol(ticks[0], fecha)
        net = obtener_net_premium(ticks[0], fecha)
        if grupo == "SPX" and "SPXW" in ticks:
            n2 = obtener_net_premium("SPXW", fecha)
            net["net_call"] += n2["net_call"]
            net["net_put"] += n2["net_put"]
            if len(n2["serie"]):
                net["serie"] = net["serie"].add(n2["serie"], fill_value=0)
        card = grafico(grupo, df, etiqueta, fecha, niv, vol, net)
        if card:
            resumen.append(card)
        if df is not None and not df.empty:
            top = df.nlargest(3, "exposicion")
            for _, r in top.iterrows():
                if r["exposicion"] >= ALERTA_USD:
                    telegram(
                        f"{grupo} {etiqueta} {fmt_usd(r['exposicion'])} "
                        f"{r['lado']} {r['contrato']} {r['hora']:%H:%M}"
                    )


def main():
    if not API_KEY:
        print("Falta UW_API_KEY")
        return
    modo = os.getenv("MODO_FLUJO", "AMBOS").upper()
    hoy, ayer = datetime.now(TZ_MERCADO).date(), dia_habil_anterior()
    print("MODO", modo, "HOY", hoy, "AYER", ayer)
    resumen = []
    if modo in ("AYER", "AMBOS"):
        procesar(ayer, "AYER", resumen)
    if modo in ("HOY", "AMBOS"):
        procesar(hoy, "HOY", resumen)
    with open(os.path.join(CARPETA, "resumen.json"), "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, default=str)
    print("resumen.json", len(resumen))


if __name__ == "__main__":
    main()