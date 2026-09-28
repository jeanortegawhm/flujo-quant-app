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
MODO = os.getenv("MODO_FLUJO", "AYER").upper()
H = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
GRUPOS = {
    "SPX": ["SPX", "SPXW"],
    "SPY": ["SPY"],
    "QQQ": ["QQQ"],
    "DIA": ["DIA"],
    "GLD": ["GLD"],
}
MIN_PREM = {"SPX": 2_000_000, "SPY": 400_000, "QQQ": 400_000, "DIA": 250_000, "GLD": 250_000}

def get(url, params=None):
    try:
        r = requests.get(url, headers=H, params=params or {}, timeout=25)
        if r.status_code != 200:
            print("  HTTP", r.status_code, url.split("/api/")[-1][:70])
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

def sesion_habil(d=None):
    d = d or datetime.now(TZ).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def ayer_habil():
    d = sesion_habil() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def fechas_modo():
    hoy, ayer = sesion_habil(), ayer_habil()
    if MODO == "HOY":
        return [hoy]
    if MODO == "AMBOS":
        return [ayer, hoy] if hoy != ayer else [hoy]
    return [ayer]

def precio_obj(obj):
    if not obj:
        return None
    if isinstance(obj, list) and obj:
        obj = obj[0]
    if not isinstance(obj, dict):
        return None
    lt = obj.get("last_trade") if isinstance(obj.get("last_trade"), dict) else {}
    for x in (obj.get("close"), obj.get("last"), obj.get("price"), obj.get("last_price"), lt.get("price")):
        v = num(x)
        if v:
            return v
    return None

def last_px(tk):
    v = precio_obj(get(f"https://api.unusualwhales.com/api/stock/{tk}/stock-state"))
    if v:
        return v
    v = precio_obj(get(f"https://api.unusualwhales.com/api/stock/{tk}/quote"))
    if v:
        return v
    v = precio_obj(get(f"https://api.unusualwhales.com/api/stock/{tk}/ohlc/1d", {"timeframe": "5D"}))
    return v

def ohlc_sesion(tk, fecha):
    raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/ohlc/5m",
              {"timeframe": "1D", "end_date": str(fecha)})
    if not isinstance(raw, list) or not raw:
        raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/ohlc/1d",
                  {"timeframe": "5D", "end_date": str(fecha)})
    rows = []
    for r in raw or []:
        ts = pd.to_datetime(r.get("start_time") or r.get("end_time") or r.get("date"), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        ts = ts.tz_convert(TZ)
        if ts.date() != fecha:
            continue
        close = num(r.get("close"))
        if close:
            rows.append({"ts": ts, "close": close, "high": num(r.get("high")) or close,
                         "low": num(r.get("low")) or close})
    return pd.DataFrame(rows)

def tape(tk, fecha, min_prem):
    a = datetime(fecha.year, fecha.month, fecha.day, 9, 30, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    b = datetime(fecha.year, fecha.month, fecha.day, 16, 5, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    raw = get("https://api.unusualwhales.com/api/option-trades", {
        "ticker_symbol": tk,
        "newer_than": a.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "older_than": b.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "min_premium": min_prem,
        "limit": 200,
    })
    if not isinstance(raw, list):
        raw = get(f"https://api.unusualwhales.com/api/stock/{tk}/flow-alerts", {
            "date": str(fecha), "limit": 200,
        })
    df = pd.DataFrame(raw if isinstance(raw, list) else [])
    if df.empty:
        return df
    for c in ("premium", "total_premium", "price", "strike", "delta"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "premium" not in df.columns and "total_premium" in df.columns:
        df["premium"] = df["total_premium"]
    return df

def net_prem(tk, fecha):
    rows = get(f"https://api.unusualwhales.com/api/stock/{tk}/net-prem-ticks", {"date": str(fecha)})
    if not isinstance(rows, list) or not rows:
        return 0.0, 0.0, 1.0
    df = pd.DataFrame(rows)
    c = float(pd.to_numeric(df.get("net_call_premium", 0), errors="coerce").fillna(0).sum())
    p = float(pd.to_numeric(df.get("net_put_premium", 0), errors="coerce").fillna(0).sum())
    bull = max(c, 0) + max(-p, 0)
    bear = max(-c, 0) + max(p, 0)
    ratio = bull / bear if bear else (2 if bull else 1)
    return c - p, ratio, c

def niveles(tk, fecha, spot):
    out = {}
    for src in ("oi", "vol"):
        d = get(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels",
                {"date": str(fecha), "source": src})
        if not isinstance(d, dict):
            continue
        for k, c in (("QF", "gamma_flip"), ("CW", "call_wall"), ("PW", "put_wall"), ("MAG", "gamma_magnet")):
            v = num(d.get(c))
            if v is None or k in out:
                continue
            if spot and abs(v - spot) / max(abs(spot), 1) > 0.06:
                continue
            out[k] = v
    return out

def semaforo(close, qf, ratio, net):
    p1 = 0
    if qf and close and abs(close - qf) / max(abs(qf), 1) >= 0.003:
        p1 = 1 if close >= qf else -1
    p2 = 1 if net > 500_000 or ratio >= 1.2 else -1 if net < -500_000 or ratio <= 0.83 else 0
    p3 = p2
    up = sum(v > 0 for v in (p1, p2, p3))
    dn = sum(v < 0 for v in (p1, p2, p3))
    if up >= 2:
        return "ALC", f"{up}/3 ALCISTA"
    if dn >= 2:
        return "BAJ", f"{dn}/3 BAJISTA"
    return "NEU", f"{max(up, dn)}/3 NEUTRO"

def grafico(grupo, fecha, px, niv, net, ratio, tapes):
    bg, fg, grid = "#0b1220", "#e8eef7", "#1d2a3d"
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12.4, 8.0), facecolor=bg,
                                   gridspec_kw={"height_ratios": [2.3, 0.9], "hspace": 0.12})
    for ax in (ax1, ax2):
        ax.set_facecolor(bg)
        ax.tick_params(colors=fg, labelsize=8)
        ax.grid(True, color=grid, alpha=0.28)
        for s in ax.spines.values():
            s.set_color(grid)
    spot = last_px(GRUPOS[grupo][0])
    col, txt = semaforo(spot, niv.get("QF"), ratio, net)
    if px is not None and not px.empty:
        ax1.plot(px["ts"], px["close"], color="#6ea8ff", lw=1.4)
        if spot:
            ax1.axhline(spot, color="#6ea8ff", ls="--", lw=0.8)
        for k, c in (("QF", "#1aa3a3"), ("CW", "#2ecc71"), ("PW", "#e74c3c"), ("MAG", "#d4af37")):
            if niv.get(k):
                ax1.axhline(niv[k], color=c, ls=":", lw=1.0)
                ax1.text(px["ts"].iloc[0], niv[k], f" {k} {niv[k]:.2f}", color=c, fontsize=7, va="bottom")
    else:
        ax1.text(0.02, 0.5, "Sin velas UW de esa sesión. Niveles GEX abajo.",
                 transform=ax1.transAxes, color="#8b9bb0")
    ax1.set_title(f"{grupo} {fecha}   |   {txt}   |   net {fmt(net)}  ratio {ratio:.2f}",
                  color=fg, loc="left", fontsize=11)
    ax1.text(0.01, 0.03, "Print grande ≠ dirección. Rojo puede ser hedge.",
             transform=ax1.transAxes, color="#d7e3f4", fontsize=8,
             bbox=dict(fc="#121b2c", ec="#2a3b55", pad=4))
    ax2.axhline(0, color=fg, lw=0.5)
    ax2.set_ylabel("NET $M", color=fg, fontsize=8)
    fig.text(0.01, 0.01, f"NY {datetime.now(TZ):%H:%M}  |  COL {datetime.now(TZ_COL):%H:%M}  |  {MODO}",
             color="#8b9bb0", fontsize=8)
    ruta = os.path.join(CARPETA, f"{grupo}_{MODO}_{fecha}_{datetime.now(TZ):%H%M%S}.png")
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(ruta, dpi=118, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    print("  PNG", ruta)
    return ruta, {
        "ticker": grupo, "modo": MODO, "fecha": str(fecha),
        "semaforo": txt, "color": col, "qdelta": float(net),
        "flow_ratio": float(ratio), "qf": niv.get("QF"), "cw": niv.get("CW"), "pw": niv.get("PW"),
        "nota": "Print ≠ dirección.",
    }

def main():
    if not API_KEY:
        print("Falta UW_API_KEY")
        raise SystemExit(2)
    print("==== FLUJO", MODO, fechas_modo())
    resumen = []
    path = os.path.join(CARPETA, "resumen.json")
    if os.path.exists(path):
        try:
            resumen = [r for r in json.loads(open(path).read()) if r.get("modo") == "SWING"]
        except Exception:
            resumen = []
    for grupo, ticks in GRUPOS.items():
        for f in fechas_modo():
            try:
                px = None
                for tk in ticks:
                    px = ohlc_sesion(tk, f)
                    if px is not None and not px.empty:
                        break
                spot = last_px(ticks[0])
                niv = {}
                net = 0.0
                ratio = 1.0
                tapes = []
                for tk in ticks:
                    n, r, _ = net_prem(tk, f)
                    net += n
                    ratio = r
                    if not niv:
                        niv = niveles(tk, f, spot)
                    tapes.append(tape(tk, f, MIN_PREM.get(grupo, 400_000)))
                _, card = grafico(grupo, f, px, niv, net, ratio, tapes)
                resumen.append(card)
                print(f"  {grupo} {f} spot={spot} net={fmt(net)} {card['semaforo']}")
            except Exception:
                print("FALLO", grupo, f)
                traceback.print_exc()
    with open(path, "w") as f:
        json.dump(resumen, f, default=str)
    print("OK flujo")

if __name__ == "__main__":
    main()
