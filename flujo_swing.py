import os, json, traceback, warnings, requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
warnings.filterwarnings("ignore")

API_KEY = os.getenv("UW_API_KEY", "")
TZ = ZoneInfo("America/New_York")
TZ_COL = ZoneInfo("America/Bogota")
CARPETA = os.getenv("FLUJOS_DIR", os.path.join(os.path.expanduser("~"), "flujos"))
os.makedirs(CARPETA, exist_ok=True)
DIAS = int(os.getenv("SWING_DIAS", "8"))
H = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
LIBROS = {
    "SPX": ["SPX", "SPXW"],
    "SPY": ["SPY"],
    "QQQ": ["QQQ"],
    "DIA": ["DIA"],
    "GLD": ["GLD"],
}

def get(url, params=None):
    try:
        r = requests.get(url, headers=H, params=params or {}, timeout=25)
        if r.status_code != 200:
            print("  HTTP", r.status_code, url.split("/api/")[-1][:60])
            return None
        p = r.json()
        return p.get("data") if isinstance(p, dict) else p
    except Exception as e:
        print("  get", e)
        return None

def num(x):
    try:
        return None if x in (None, "") else float(x)
    except Exception:
        return None

def fmt(x):
    x = float(x or 0)
    s = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1e9: return f"{s}${x/1e9:.2f}B"
    if x >= 1e6: return f"{s}${x/1e6:.1f}M"
    if x >= 1e3: return f"{s}${x/1e3:.0f}k"
    return f"{s}${x:,.0f}"

def habiles(n):
    d = datetime.now(TZ).date()
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))

def ohlc_rango(tk, fechas):
    a, b = str(fechas[0]), str(fechas[-1])
    out = {}
    raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/ohlc/1d", {"date": a, "end_date": b, "limit": 40})
    if not isinstance(raw, list) or not raw:
        raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/ohlc/1h", {"date": a, "end_date": b, "limit": 400})
    if not isinstance(raw, list) or not raw:
        raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/ohlc/5m", {"date": b, "end_date": b, "limit": 200})
    rows = []
    for r in raw or []:
        tcol = r.get("start_time") or r.get("end_time") or r.get("date") or r.get("timestamp")
        ts = pd.to_datetime(tcol, utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        ts = ts.tz_convert(TZ)
        close = num(r.get("close"))
        if not close:
            continue
        rows.append({
            "d": ts.date(),
            "open": num(r.get("open")) or close,
            "high": num(r.get("high")) or close,
            "low": num(r.get("low")) or close,
            "close": close,
        })
    if rows:
        df = pd.DataFrame(rows).groupby("d", as_index=False).agg(
            open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"),
        )
        for _, r in df.iterrows():
            out[r["d"]] = {"open": float(r["open"]), "high": float(r["high"]),
                           "low": float(r["low"]), "close": float(r["close"])}
    if fechas[-1] not in out:
        d = get(f"https://api.unusualwhales.com/api/stock/{tk}/stock-state") or {}
        if not isinstance(d, dict) or not d:
            d = get(f"https://api.unusualwhales.com/api/stock/{tk}/quote") or {}
        v = None
        if isinstance(d, dict):
            lt = d.get("last_trade") if isinstance(d.get("last_trade"), dict) else {}
            for x in (d.get("close"), d.get("last"), d.get("last_price"), d.get("price"), lt.get("price")):
                v = num(x)
                if v:
                    break
        if v:
            out[fechas[-1]] = {"open": v, "high": v, "low": v, "close": v}
            print("  quote fallback", tk, v)
    print("  velas", tk, len(out), "días")
    return out

def net_dia(tk, fecha):
    rows = get(f"https://api.unusualwhales.com/api/stock/{tk}/net-prem-ticks", {"date": str(fecha)})
    if not isinstance(rows, list) or not rows:
        return {"call": 0.0, "put": 0.0, "net": 0.0, "ratio": 1.0}
    df = pd.DataFrame(rows)
    c = float(pd.to_numeric(df.get("net_call_premium", 0), errors="coerce").fillna(0).sum())
    p = float(pd.to_numeric(df.get("net_put_premium", 0), errors="coerce").fillna(0).sum())
    bull = max(c, 0) + max(-p, 0)
    bear = max(-c, 0) + max(p, 0)
    ratio = bull / bear if bear else (2 if bull else 1)
    return {"call": c, "put": p, "net": c - p, "ratio": float(ratio)}

def niveles(tk, fecha):
    out = {}
    for src in ("oi", "vol"):
        d = get(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
                {"date": str(fecha), "source": src})
        if not isinstance(d, dict):
            continue
        for k, c in (("QF", "gamma_flip"), ("CW", "call_wall"), ("PW", "put_wall")):
            v = num(d.get(c))
            if v is not None and k not in out:
                out[k] = v
    return out

def iv30(tk, fecha):
    rows = get(f"https://api.unusualwhales.com/api/stock/{tk}/interpolated-iv", {"date": str(fecha)})
    if not isinstance(rows, list) or not rows:
        return None, None
    d30 = next((r for r in rows if num(r.get("days")) == 30), rows[-1])
    iv = num(d30.get("volatility"))
    ivp = num(d30.get("percentile"))
    if iv and iv < 3:
        iv *= 100
    return iv, ivp

def sesgo_fila(close, qf, ratio, net, ivp):
    p1 = 0
    if qf and close:
        dist = abs(close - qf) / max(abs(qf), 1)
        if dist >= 0.003:
            p1 = 1 if close >= qf else -1
    p2 = 1 if net > 400_000 or ratio >= 1.20 else -1 if net < -400_000 or ratio <= 0.83 else 0
    p3 = 0 if (ivp and ivp >= 85) else p2
    up = sum(v > 0 for v in (p1, p2, p3))
    dn = sum(v < 0 for v in (p1, p2, p3))
    if up >= 2 and up > dn:
        return p1, p2, p3, "ALC", f"{up}/3 ALCISTA"
    if dn >= 2 and dn > up:
        return p1, p2, p3, "BAJ", f"{dn}/3 BAJISTA"
    return p1, p2, p3, "NEU", f"{max(up, dn)}/3 NEUTRO"

def serie_grupo(grupo, ticks, fechas):
    cache = {}
    for tk in ticks:
        cache.update(ohlc_rango(tk, fechas))
        if cache:
            break
    rows = []
    for f in fechas:
        call = put = 0.0
        niv, bar = {}, cache.get(f)
        for tk in ticks:
            try:
                n = net_dia(tk, f)
                call += n["call"]
                put += n["put"]
                if not niv:
                    niv = niveles(tk, f)
            except Exception as e:
                print("  skip", tk, f, e)
        net = call - put
        bull = max(call, 0) + max(-put, 0)
        bear = max(-call, 0) + max(put, 0)
        ratio = bull / bear if bear else (2 if bull else 1.0)
        close = bar["close"] if bar else None
        qf = niv.get("QF")
        if qf and bar:
            if not (bar["low"] * 0.99 <= qf <= bar["high"] * 1.01):
                qf = None
        iv, ivp = iv30(ticks[0], f)
        p1, p2, p3, col, txt = sesgo_fila(close, qf, ratio, net, ivp)
        rows.append({
            "fecha": f, "close": close,
            "open": bar["open"] if bar else None,
            "high": bar["high"] if bar else None,
            "low": bar["low"] if bar else None,
            "call": call, "put": put, "net": net, "ratio": ratio,
            "qf": qf, "cw": niv.get("CW"), "pw": niv.get("PW"),
            "iv": iv, "ivp": ivp, "p1": p1, "p2": p2, "p3": p3,
            "color": col, "texto": txt,
        })
        print(f"  {grupo} {f}  close={close}  net={fmt(net)}  {txt}")
    return pd.DataFrame(rows)

def veredicto(df):
    if df is None or df.empty:
        return "NEU", "Sin días", "No hay datos UW de swing."
    ult = df.iloc[-1]
    n3 = df.tail(3)
    alc = int((n3["color"] == "ALC").sum())
    baj = int((n3["color"] == "BAJ").sum())
    net3 = float(n3["net"].sum())
    if alc >= 2 and net3 > 0:
        return "ALC", "SWING ALCISTA", f"2+ días alcistas. Prima neta 3d {fmt(net3)}. Un 0DTE no abre esto."
    if baj >= 2 and net3 < 0:
        return "BAJ", "SWING BAJISTA", f"2+ días bajistas. Prima neta 3d {fmt(net3)}. Un 0DTE no abre esto."
    return "NEU", "SWING NEUTRO", f"Último {ult['texto']}. Prima 3d {fmt(net3)}. Espera 2 de 3 días."

def grafico(grupo, df):
    bg, fg, grid = "#0b1220", "#e8eef7", "#1d2a3d"
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12.2, 8.2), facecolor=bg,
                                   gridspec_kw={"height_ratios": [2.2, 1.1], "hspace": 0.12})
    for ax in (ax1, ax2):
        ax.set_facecolor(bg)
        ax.tick_params(colors=fg, labelsize=8)
        ax.grid(True, color=grid, alpha=0.28)
        for s in ax.spines.values():
            s.set_color(grid)
    ruta = os.path.join(CARPETA, f"{grupo}_SWING_{datetime.now(TZ):%Y%m%d_%H%M%S}.png")
    x = np.arange(len(df)) if not df.empty else np.array([])
    sin_px = df.empty or df["close"].isna().all()
    col, tit, nota = veredicto(df)
    if sin_px:
        ax1.set_title(f"{grupo} SWING   |   {col} {tit}   |   solo prima neta", color=fg, loc="left", fontsize=11)
        ax1.text(0.01, 0.5, "UW no dio velas 1d. Abajo sí está el flujo.",
                 transform=ax1.transAxes, color="#8b9bb0", fontsize=9)
    else:
        ax1.plot(x, df["close"], color="#6ea8ff", lw=1.8)
        if df["low"].notna().any() and df["high"].notna().any():
            ax1.fill_between(x, df["low"].fillna(df["close"]), df["high"].fillna(df["close"]),
                             color="#6ea8ff", alpha=0.08)
        qf = df["qf"].dropna().iloc[-1] if df["qf"].notna().any() else None
        if qf:
            ax1.axhline(qf, color="#1aa3a3", ls="--", lw=1.1)
            ax1.text(0, qf, f" QF {qf:.2f} ", color="white", fontsize=8, va="bottom",
                     bbox=dict(fc="#1aa3a3", ec="none", pad=0.2))
        ax1.set_title(f"{grupo} SWING {df['fecha'].iloc[0]} → {df['fecha'].iloc[-1]}   |   {col} {tit}",
                      color=fg, loc="left", fontsize=11, pad=6)
    if len(x):
        ax1.set_xticks(x)
        ax1.set_xticklabels([pd.Timestamp(d).strftime("%m-%d") for d in df["fecha"]])
        ax1.text(0.01, 0.03, nota, transform=ax1.transAxes, color="#d7e3f4", fontsize=8,
                 va="bottom", bbox=dict(fc="#121b2c", ec="#2a3b55", pad=4, alpha=0.92))
        nets = df["net"].fillna(0).values / 1e6
        ax2.bar(x, nets, color=["#2ecc71" if v >= 0 else "#e74c3c" for v in nets], width=0.7)
        ax2.axhline(0, color=fg, lw=0.5)
        ax2.set_xticks(x)
        ax2.set_xticklabels([pd.Timestamp(d).strftime("%m-%d") for d in df["fecha"]])
    ax1.set_ylabel("PRECIO", color=fg, fontsize=8)
    ax2.set_ylabel("NET PREM $M", color=fg, fontsize=8)
    fig.text(0.01, 0.01,
             f"NY {datetime.now(TZ):%H:%M}  |  COL {datetime.now(TZ_COL):%H:%M}  |  "
             f"swing = varios días  |  un C+0d no abre swing",
             color="#8b9bb0", fontsize=8)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(ruta, dpi=118, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    print("  PNG", ruta)
    if df.empty:
        return ruta, {}
    ult = df.iloc[-1]
    return ruta, {
        "ticker": grupo, "modo": "SWING", "fecha": str(ult["fecha"]),
        "semaforo": tit, "color": col,
        "p1": int(ult["p1"]), "p2": int(ult["p2"]), "p3": int(ult["p3"]),
        "qdelta": float(df["net"].sum()), "qd30": float(ult["net"]),
        "flow_ratio": float(ult["ratio"]),
        "iv": ult["iv"], "ivp": ult["ivp"],
        "cw": ult["cw"], "pw": ult["pw"], "qf": ult["qf"],
        "nota": nota,
    }

def main():
    if not API_KEY:
        print("Falta UW_API_KEY")
        raise SystemExit(2)
    fechas = habiles(DIAS)
    print("==== SWING", fechas[0], "→", fechas[-1])
    resumen = []
    path_old = os.path.join(CARPETA, "resumen.json")
    if os.path.exists(path_old):
        try:
            resumen = [r for r in json.loads(open(path_old).read()) if r.get("modo") != "SWING"]
        except Exception:
            resumen = []
    for grupo, ticks in LIBROS.items():
        try:
            df = serie_grupo(grupo, ticks, fechas)
            _, card = grafico(grupo, df)
            if card:
                resumen.append(card)
        except Exception:
            print("FALLO", grupo)
            traceback.print_exc()
    with open(path_old, "w") as f:
        json.dump(resumen, f, default=str)
    print("OK swing")

if __name__ == "__main__":
    main()
