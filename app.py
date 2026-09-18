import os, sys, json, subprocess
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st

st.set_page_config(page_title="Flujo Quant", page_icon="Q", layout="wide")
HOME = Path(__file__).resolve().parent
CARPETA = HOME / "flujos"
CARPETA.mkdir(exist_ok=True)

st.markdown("""
<style>
.stApp { background:#0b1220; color:#e8eef7; }
h1, h2, h3 { letter-spacing:.02em; }
.q-hero {
  background: linear-gradient(90deg,#0b1220 0%,#121b2c 55%,#0b1220 100%);
  border:1px solid #243044; border-radius:14px; padding:18px 22px 16px; margin-bottom:12px;
}
.q-kicker { color:#d4af37; font-size:12px; letter-spacing:.22em; font-weight:700; }
.q-title { color:#e8eef7; font-size:34px; font-weight:750; margin:2px 0 4px; }
.q-title span { color:#5ec8c6; }
.q-sub { color:#8b9bb0; font-size:14px; }
.q-box {
  background:#121b2c; border:1px solid #243044; border-radius:12px;
  padding:12px 14px; margin:8px 0 14px; color:#c9d6e8; font-size:14px; line-height:1.45;
}
.q-box b { color:#d4af37; }
.q-box i { color:#5ec8c6; font-style:normal; }
</style>
""", unsafe_allow_html=True)

def secreto(n, d=""):
    try:
        return st.secrets.get(n, os.getenv(n, d))
    except Exception:
        return os.getenv(n, d)

def fmt_num(x, dec=0):
    try:
        x = float(x)
    except Exception:
        return "—"
    if abs(x) >= 1e9: return f"${x/1e9:.2f}B"
    if abs(x) >= 1e6: return f"${x/1e6:.1f}M"
    if abs(x) >= 1000: return f"${x:,.0f}"
    return f"{x:.{dec}f}"

if st.sidebar.text_input("Clave", type="password") != secreto("APP_PASSWORD", "cambiaesta"):
    st.stop()

st.sidebar.header("Franja / tamaño")
franja_alto = st.sidebar.slider("Alto de la franja", 0.20, 1.20, 0.26, 0.02)
franja_min = st.sidebar.slider("Ancho bloque (min)", 1, 8, 5, 1)
fig_ancho = st.sidebar.slider("Ancho del gráfico", 10.0, 16.0, 12.4, 0.2)
fig_alto = st.sidebar.slider("Alto del gráfico", 10.0, 16.0, 12.0, 0.2)

def correr(nombre, modo):
    script = HOME / nombre
    if not script.exists():
        st.error("Falta " + script.name)
        return
    box = st.empty()
    box.info(f"{nombre} · {modo}")
    env = os.environ.copy()
    env["UW_API_KEY"] = secreto("UW_API_KEY", "")
    env["TELEGRAM_BOT_TOKEN"] = secreto("TELEGRAM_BOT_TOKEN", "")
    env["TELEGRAM_CHAT_ID"] = secreto("TELEGRAM_CHAT_ID", "")
    env["FLUJOS_DIR"] = str(CARPETA)
    env["MODO_FLUJO"] = modo
    env["MIN_PREMIUM"] = os.environ.get("MIN_PREMIUM", "250000")
    env["SOLO_0DTE"] = os.environ.get("SOLO_0DTE", "0")
    env["UMBRAL_BURBUJA"] = os.environ.get("UMBRAL_BURBUJA", "0")
    env["ALERTA_USD"] = os.environ.get("ALERTA_USD", "2000000")
    env["FRANJA_ALTO"] = str(franja_alto)
    env["FRANJA_MIN"] = str(franja_min)
    env["FIG_ANCHO"] = str(fig_ancho)
    env["FIG_ALTO"] = str(fig_alto)
    p = subprocess.run(
        [sys.executable, "-u", str(script)],
        capture_output=True, text=True, cwd=str(HOME), env=env,
    )
    box.empty()
    st.success("Listo") if p.returncode == 0 else st.error("Error")
    if p.stdout:
        st.text_area("Salida", p.stdout[-3500:], height=160)
    if p.stderr:
        st.text_area("Avisos", p.stderr[-1200:], height=90)

st.markdown("""
<div class="q-hero">
  <div class="q-kicker">QUANT FLOW</div>
  <div class="q-title"><span>Flujo</span> Quant</div>
  <div class="q-sub">Tape · GEX · OI · Dark pool — el print no es la dirección del ETF</div>
</div>
""", unsafe_allow_html=True)

ny = datetime.now(ZoneInfo("America/New_York"))
abierto = ny.weekday() < 5 and ny.replace(hour=9, minute=30) <= ny <= ny.replace(hour=16, minute=0)
a, b, c, d = st.columns(4)
a.metric("NY", ny.strftime("%H:%M"))
b.metric("Colombia", datetime.now(ZoneInfo("America/Bogota")).strftime("%H:%M"))
c.metric("Mercado", "ABIERTO" if abierto else "CERRADO")
d.metric("Franja", f"{franja_alto:.2f} / {franja_min}m")

t1, t2, t3, t4, t5 = st.tabs(["Flujo", "Swing", "Dashboard", "Libros QQQ/NDX", "GEX · OI · DP"])

with t1:
    st.markdown("""
    <div class="q-box">
    <b>Qué estás viendo.</b> El gráfico tipo Quantium / Gregory: precio, anillos de prima,
    franja de agresor, QD notional y TOTAL. No es una señal de compra/venta.<br><br>
    <b>Cómo leerlo.</b><br>
    • <i>Anillo oro</i> = print grande (prima). Triángulo verde = sesgo call/compra. Rojo = put/venta.<br>
    • <i>CW / PW / QF</i> = call wall, put wall, gamma flip. Debajo del QF el dealer suele amplificar.<br>
    • <i>Agresor</i> oro/teal = más prima call neta. Morado = más put.<br>
    • <i>QD</i> verde/rojo = prima firmada cada 5 min. <i>TOTAL</i> oro = tamaño del print.<br>
    • Recuadro abajo a la izquierda: si el tape <b>acompañó</b> la mecha o si fue GEX / futuros / hueco.<br><br>
    <b>Regla.</b> Print ≠ dirección del ETF. Un put en GLD o un call vendido en IBIT no mandan el spot.
    </div>
    """, unsafe_allow_html=True)
    f1, f2, f3, f4b = st.columns(4)
    min_p = f1.number_input("Prima mín $", 50000, 3000000, 250000, 10000)
    umb = f2.number_input("Burbuja $M (0=auto)", 0, 500, 0, 5)
    alerta = f3.number_input("Alerta Telegram $M", 0.5, 20.0, 2.0, 0.5)
    dte = f4b.checkbox("Solo 0DTE/1DTE")
    os.environ["MIN_PREMIUM"] = str(min_p)
    os.environ["UMBRAL_BURBUJA"] = str(int(umb) * 1_000_000)
    os.environ["ALERTA_USD"] = str(int(alerta * 1_000_000))
    os.environ["SOLO_0DTE"] = "1" if dte else "0"
    if st.checkbox("Vivo 3 min (solo HOY)") and abierto:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=180000, key="vivo")
            correr("flujo2.py", "HOY")
        except Exception as e:
            st.error(e)
    x, y, z = st.columns(3)
    if x.button("Solo AYER", width="stretch"):
        correr("flujo2.py", "AYER")
    if y.button("AYER + HOY", type="primary", width="stretch"):
        correr("flujo2.py", "AMBOS")
    if z.button("Solo HOY", width="stretch"):
        correr("flujo2.py", "HOY")

with t2:
    st.markdown("""
    <div class="q-box">
    <b>Qué estás viendo.</b> Posicionamiento de varios días para swing: paredes, QF y flujo neto,
    no el tape de 1 minuto.<br><br>
    <b>Cómo leerlo.</b> Úsalo para el sesgo de 2–10 días. Si el intradía pelea con el swing,
    manda el intradía solo dentro del día. No abras swing por un print 0DTE.
    </div>
    """, unsafe_allow_html=True)
    if st.button("Generar Swing", width="stretch"):
        if (HOME / "flujo_swing.py").exists():
            correr("flujo_swing.py", "AMBOS")
        else:
            st.warning("Falta flujo_swing.py")

with t3:
    st.markdown("""
    <div class="q-box">
    <b>Qué estás viendo.</b> Resumen de todos los tickers después de correr Flujo.
    Cards con semáforo 3 pilares + el PNG.<br><br>
    <b>Cómo leerlo.</b><br>
    • <i>P1</i> posicionamiento (precio vs QF / PW / CW).<br>
    • <i>P2</i> flujo de los últimos 30 min + flow ratio.<br>
    • <i>P3</i> volatilidad / implied move.<br>
    • Operable solo con <b>2 de 3</b>. 1/3 = no hay tesis, solo ruido.<br>
    • IVP alto + debajo del QF = rango amplio, no persigas.
    </div>
    """, unsafe_allow_html=True)
    res = CARPETA / "resumen.json"
    if res.exists():
        try:
            data = json.loads(res.read_text())
        except Exception:
            data = []
        cols = st.columns(3)
        for i, row in enumerate(data[-12:]):
            with cols[i % 3]:
                st.markdown(f"**{row.get('color', '')} {row.get('ticker')} {row.get('modo')}**")
                st.caption(row.get("semaforo", "—"))
                st.write(f"P1 {row.get('p1')} · P2 {row.get('p2')} · P3 {row.get('p3')}")
                st.write(f"CW {row.get('cw') or '—'} · PW {row.get('pw') or '—'} · QF {row.get('qf') or '—'}")
                st.write(f"qΔ {fmt_num(row.get('qdelta'))} · 30m {fmt_num(row.get('qd30'))}")
                ivp, ivr, im = row.get("ivp"), row.get("ivr"), row.get("imp_move_pct")
                ivp_txt = f"{float(ivp):.0f}" if ivp is not None else "—"
                ivr_txt = f"{float(ivr):.0f}" if ivr is not None else "—"
                im_txt = f"{float(im)*100:.2f}%" if im else "—"
                st.write(f"IVP {ivp_txt} · IVR {ivr_txt} · IM {im_txt}")
                st.write(f"flow {float(row.get('flow_ratio') or 0):.2f}")
                if row.get("nota"):
                    st.caption(row.get("nota"))
    else:
        st.info("Aún no hay resumen.json. Corre Solo AYER o Solo HOY.")
    pngs = sorted(CARPETA.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in pngs[:8]:
        if p.stat().st_size <= 3_500_000:
            st.image(str(p), caption=p.name, width="stretch")

with t4:
    st.markdown("""
    <div class="q-box">
    <b>Qué estás viendo.</b> El mismo mercado en <i>dos libros</i>: QQQ vs NDX, SPY vs SPX, IWM vs RUT.
    Hunab / Angel: más niveles no es más claridad. A veces el ETF muestra la zona y el índice no.<br><br>
    <b>Cómo leerlo.</b> Si QQQ arma pared debajo del precio y NDX no, esa zona es filtro, no orden.
    Confirma después en Flujo (print) o en Dark pool. No compres el nivel solo porque existe.
    </div>
    """, unsafe_allow_html=True)
    os.environ["UW_API_KEY"] = secreto("UW_API_KEY", "")
    try:
        from paneles import PARES, gex_niveles, gex_strikes, fig_gex
        hoy = datetime.now(ZoneInfo("America/New_York")).date()
        for par_a, par_b in PARES:
            c1, c2 = st.columns(2)
            for col, tk in ((c1, par_a), (c2, par_b)):
                with col:
                    niv = gex_niveles(tk, hoy)
                    spot = niv.get("gamma_flip") or niv.get("call_wall")
                    df = gex_strikes(tk, hoy, spot)
                    st.pyplot(fig_gex(tk, df, niv, spot), width="stretch")
    except Exception as e:
        st.error(e)

with t5:
    st.markdown("""
    <div class="q-box">
    <b>Qué estás viendo.</b> Confirmación, no el tape. Perfil GEX por strike (Gexbot/SpotGamma),
    OI call vs put, y dark pool agrupado por precio.<br><br>
    <b>Cómo leerlo.</b><br>
    • Barras <i>verdes</i> = call GEX. <i>Moradas</i> = put GEX. El QF es donde cambia el régimen.<br>
    • Un PW lejos (GLD 200) se ignora en el zoom; no estira el eje.<br>
    • OI alto en puts no es automáticamente bajista: puede ser hedge de un long de spot.<br>
    • Dark pool = zonas donde cruzó size opaco. Es mapa de liquidez, no dirección.
    </div>
    """, unsafe_allow_html=True)
    os.environ["UW_API_KEY"] = secreto("UW_API_KEY", "")
    tk = st.selectbox("Ticker", ["QQQ", "NDX", "SPY", "SPX", "IWM", "IBIT", "GLD"])
    try:
        from paneles import gex_niveles, gex_strikes, oi_vol, darkpool, fig_gex, fig_oi, fig_dp
        hoy = datetime.now(ZoneInfo("America/New_York")).date()
        g = gex_niveles(tk, hoy)
        o = oi_vol(tk, hoy)
        spot = g.get("gamma_flip") or g.get("call_wall")
        df = gex_strikes(tk, hoy, spot)
        a, b, c, d = st.columns(4)
        a.metric("Call wall", str(g.get("call_wall", "—")))
        b.metric("Put wall", str(g.get("put_wall", "—")))
        c.metric("Gamma flip", str(g.get("gamma_flip", "—")))
        d.metric("Magnet", str(g.get("gamma_magnet", "—")))
        e, f, g2, h = st.columns(4)
        e.metric("Call OI", f"{float(o.get('call_open_interest') or 0):,.0f}")
        f.metric("Put OI", f"{float(o.get('put_open_interest') or 0):,.0f}")
        g2.metric("Call vol", f"{float(o.get('call_volume') or 0):,.0f}")
        h.metric("Put vol", f"{float(o.get('put_volume') or 0):,.0f}")
        st.pyplot(fig_gex(tk, df, g, spot), width="stretch")
        st.pyplot(fig_oi(tk, o), width="stretch")
        st.pyplot(fig_dp(tk, darkpool(tk)), width="stretch")
    except Exception as e:
        st.error(e)