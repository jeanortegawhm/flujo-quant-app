import os, time, warnings, requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
MAX_ETIQUETAS = 6
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
    data = get_json(
        f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
        {"date": str(fecha), "source": "oi"},
    )
    if not isinstance(data, dict):
        data = get_json(
            f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
            {"date": str(fecha), "source": "vol"},
        )
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
    d = get_json(
        f"https://api.unusualwhales.com/api/stock/{tk}/volatility/stats",
        {"date": str(fecha)},
    )
    if isinstance(d, dict):
        out["iv"] = pct(d.get("iv"))
        out["ivr"] = pct(d.get("iv_rank"))
    rows = get_json(
        f"https://api.unusualwhales.com/api/stock/{tk}/interpolated-iv",
        {"date": str(fecha)},
    )
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
    if qf and last < qf:
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
        senales.append("IVR_ALTA")
    elif ivr is not None and ivr <= 25:
        senales.append("IVR_BAJA")
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
    ax.text(
        0.01, y, f"{nombre} {y:.2f}",
        transform=ax.get_yaxis_transform(),
        color="white", fontsize=8, fontweight="bold", va="bottom",
        bbox=dict(fc=color, ec="none", pad=0.25),
    )


def grafico_swing(grupo, px, df, niveles, vol):
    if px.empty:
        print("  sin precio", grupo)
        return
    bg, fg, grid = "#0b1220", "#e8eef7", "#1d2a3d"
    last = float(px["Close"].iloc[-1])
    umbral = MIN_BURBUJA[grupo]

    if df is None or df.empty:
        q5 = q15 = 0.0
        grandes = pd.DataFrame()
        diario = pd.Series(dtype=float)
    else:
        df = df.copy()
        df["dia"] = pd.to_datetime(df["dia"])
        diario = df.groupby(df["dia"].dt.date)["qdelta"].sum()
        ult = sorted(diario.index)[-5:] if len(diario) else []
        q5 = float(diario.loc[diario.index.isin(ult)].sum()) if ult else 0.0
        q15 = float(diario.sum())
        grandes = df[df["exposicion"] >= umbral].sort_values("exposicion", ascending=False)
        if grandes.empty:
            grandes = df.nlargest(6, "exposicion")

    sesgo, senales = senal_swing(px, last, niveles, q5, q15, vol)
    ivtxt = []
    if vol.get("iv") is not None:
        ivtxt.append(f"IV {vol['iv']:.1f}%")
    if vol.get("ivr") is not None:
        ivtxt.append(f"IVR {vol['ivr']:.0f}")

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(13, 7.2), sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1.1]}, facecolor=bg,
    )
    for ax in (ax1, ax2):
        ax.set_facecolor(bg)
        ax.grid(True, color=grid)
        ax.tick_params(colors=fg)
        for s in ax.spines.values():
            s.set_color(grid)

    x = px.index
    if "Open" in px.columns and "High" in px.columns:
        up = px["Close"] >= px["Open"]
        ax1.bar(x[up], px.loc[up, "High"] - px.loc[up, "Low"], bottom=px.loc[up, "Low"],
                width=0.6, color="#2ecc71", alpha=0.35)
        ax1.bar(x[~up], px.loc[~up, "High"] - px.loc[~up, "Low"], bottom=px.loc[~up, "Low"],
                width=0.6, color="#e74c3c", alpha=0.35)
    ax1.plot(x, px["Close"], color="#7eb6ff", lw=1.6)

    lo, hi = float(px["Low"].min()) if "Low" in px.columns else float(px["Close"].min()), \
             float(px["High"].max()) if "High" in px.columns else float(px["Close"].max())
    pad = (hi - lo) * 0.08 or 1
    ax1.set_ylim(lo - pad, hi + pad)

    if not grandes.empty:
        top = grandes.nlargest(MAX_ETIQUETAS, "exposicion")
        for _, r in grandes.iterrows():
            col = "#2ecc71" if r["signo"] > 0 else "#e74c3c"
            mk = "^" if r["signo"] > 0 else "v"
            ax1.scatter(pd.Timestamp(r["dia"]), r["spot"] or last,
                        s=50, c=col, marker=mk, zorder=5, alpha=0.85)
        for _, r in top.iterrows():
            ax1.annotate(
                fmt_usd(r["exposicion"]),
                (pd.Timestamp(r["dia"]), r["spot"] or last),
                textcoords="offset points", xytext=(4, 8),
                color=fg, fontsize=7,
            )

    if niveles.get("PW"):
        pintar_nivel(ax1, niveles["PW"], "PW", "#e74c3c")
    if niveles.get("QF"):
        pintar_nivel(ax1, niveles["QF"], "QF", "#f1c40f")
    if niveles.get("CW"):
        pintar_nivel(ax1, niveles["CW"], "CW", "#2ecc71")

    if len(diario):
        idx = pd.to_datetime(list(diario.index))
        ax2.bar(idx, diario.values / 1e6,
                color=["#2ecc71" if v >= 0 else "#e74c3c" for v in diario.values],
                width=0.7, alpha=0.85)
    ax2.axhline(0, color=fg, lw=0.5)
    ax2.set_ylabel("qΔ $M", color=fg)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

    ax1.set_title(
        f"SWING {grupo}  |  {DIAS} sesiones  |  {sesgo}  |  q5 {fmt_usd(q5)}  q{DIAS} {fmt_usd(q15)}  "
        + "  ".join(ivtxt),
        color=fg, loc="left", fontsize=11,
    )
    ax1.set_ylabel("Precio", color=fg)
    fig.text(
        0.01, 0.01,
        f"NY {datetime.now(TZ):%H:%M}  |  COL {datetime.now(TZ_COL):%H:%M}  |  "
        f"{', '.join(senales)}  |  print ≠ dirección ETF",
        color="#9aa7b8", fontsize=8,
    )
    fig.tight_layout()
    ruta = os.path.join(CARPETA, f"{grupo}_SWING_{datetime.now(TZ):%Y%m%d_%H%M%S}.png")
    fig.savefig(ruta, dpi=110, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    print("  PNG", ruta)
    print(" ", sesgo, "|", ", ".join(senales))


def procesar_grupo(grupo, ticks, dias):
    print("==== SWING", grupo)
    frames = []
    for d in dias:
        for tk in ticks:
            t = tape_dia(d, tk)
            if t is not None and not t.empty:
                frames.append(t)
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    px = precio_diario(grupo)
    last = float(px["Close"].iloc[-1]) if not px.empty else None
    fecha = dias[-1]
    niv = {}
    for tk in ticks:
        n = obtener_niveles(tk, fecha, last)
        for k, v in n.items():
            if k not in niv:
                niv[k] = v
    vol = obtener_vol(ticks[0], fecha)
    grafico_swing(grupo, px, df, niv, vol)


def main():
    if not API_KEY:
        print("Falta UW_API_KEY")
        return
    dias = habiles(DIAS)
    print("SWING días", dias[0], "→", dias[-1])
    for grupo, ticks in GRUPOS.items():
        procesar_grupo(grupo, ticks, dias)


if __name__ == "__main__":
    main()