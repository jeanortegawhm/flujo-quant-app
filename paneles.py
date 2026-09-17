import os, requests, pandas as pd

API = os.getenv("UW_API_KEY", "")
H = {"Authorization": f"Bearer {API}", "Accept": "application/json"}

def get(url, params=None):
    try:
        r = requests.get(url, headers=H, params=params or {}, timeout=20)
        if r.status_code != 200:
            return None
        p = r.json()
        return p.get("data") if isinstance(p, dict) else p
    except Exception:
        return None

def gex_niveles(tk, fecha=None):
    params = {"source": "oi"}
    if fecha:
        params["date"] = str(fecha)
    return get(f"https://api.unusualwhales.com/api/stock/{tk}/gex-levels", params) or {}

def oi_vol(tk, fecha=None):
    params = {}
    if fecha:
        params["date"] = str(fecha)
    return get(f"https://api.unusualwhales.com/api/stock/{tk}/options-volume", params) or {}

def darkpool(tk, limit=20):
    data = get(f"https://api.unusualwhales.com/api/darkpool/{tk}", {"limit": limit})
    if not isinstance(data, list):
        data = get("https://api.unusualwhales.com/api/darkpool/recent", {"limit": limit, "ticker_symbol": tk})
    if not isinstance(data, list):
        return pd.DataFrame()
    return pd.DataFrame(data).head(limit)

PARES = [("QQQ", "NDX"), ("SPY", "SPX"), ("IWM", "RUT")]
