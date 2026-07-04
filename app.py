# -*- coding: utf-8 -*-
"""Calculadora de tasas — CER + Tasa Fija en vivo.
BCRA (tira CER) + rendimientos.co (bases CER) + ArgentinaDatos (letras) + data912 (precios)."""
import requests, pandas as pd, numpy as np
from datetime import date, timedelta
from scipy.optimize import brentq
import altair as alt
import streamlit as st
import os
import urllib3; urllib3.disable_warnings()

st.set_page_config(page_title="Calculadora Esta Todo Bien Loko", layout="wide")

BCRA = "https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias"
HDRS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
LAG = 10

# --- exclusiones e instrumentos nuevos -------------------------------------
EXCLUIR_FIJA = ["T31Y7", "T30J6"]     # T31Y7=TAMAR ; T30J6=vencido
EXCLUIR_CER  = ["TZX26"]              # vencido
NUEVOS_CER = {  # ticker: (emision, vencimiento) — cupón cero
    "TZXS7": ("2026-03-31", "2027-09-30"), "TZXS8": ("2026-03-31", "2028-09-29"),
    "TZXM8": ("2026-03-31", "2028-03-31"), "TZXM9": ("2026-03-31", "2029-03-28"),
}
NUEVOS_FIJA = {  # ticker: (emision, vencimiento, TEM%) — letra tasa fija nueva
    "S13N6": ("2026-06-30", "2026-11-13", 2.10),
}
# bonos TAMAR puros: emisión + margen. vt_ref = VT de referencia (Docta) para validar.
SEED_TAMAR = [
    {"ticker": "M31G6", "emision": "2025-11-09", "vencimiento": "2026-08-31", "margen": 5.0, "vt_ref": 129.70},
    {"ticker": "TMF27", "emision": "2026-02-13", "vencimiento": "2027-02-26", "margen": 6.5, "vt_ref": 136.42},
    {"ticker": "TMG27", "emision": "2026-04-01", "vencimiento": "2027-08-31", "margen": 6.0, "vt_ref": 149.36},
    {"ticker": "TMF28", "emision": "2026-04-19", "vencimiento": "2028-02-29", "margen": 6.5, "vt_ref": 170.23},
    {"ticker": "TMG28", "emision": "2026-04-30", "vencimiento": "2028-08-31", "margen": 6.5, "vt_ref": None},
]

# ----------------------------------------------------------------- estilo
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
html, body, [class*="css"], .stApp { font-family:'IBM Plex Sans',system-ui,sans-serif; }
.stApp { background:#ffffff; color:#16181d; }
.block-container { padding-top:2.2rem; padding-bottom:2rem; max-width:1220px; }
h1 { font-weight:600; letter-spacing:-.02em; font-size:1.35rem; margin-bottom:.2rem; }
section[data-testid="stSidebar"] { background:#fafafb; border-right:1px solid #ececf0; }
section[data-testid="stSidebar"] * { font-size:.86rem; }
[data-testid="stMetric"] { background:transparent; border:none; border-bottom:1px solid #ececf0;
    border-radius:0; padding:6px 4px 12px; }
[data-testid="stMetricValue"] { font-family:'IBM Plex Mono',monospace; font-variant-numeric:tabular-nums;
    font-size:1.15rem; font-weight:500; }
[data-testid="stMetricLabel"] { text-transform:uppercase; letter-spacing:.1em; font-size:.62rem;
    font-weight:600; color:#9096a1; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { font-family:'IBM Plex Mono',monospace; }
.stTabs [data-baseweb="tab-list"] { gap:6px; border-bottom:1px solid #ececf0; }
.stTabs [data-baseweb="tab"] { font-weight:500; font-size:.9rem; color:#6b7280; }
.stTabs [aria-selected="true"] { color:#16181d; }
hr { margin:.6rem 0; border-color:#ececf0; }
.stCaption, .stCaption p { color:#9096a1 !important; }
.stApp { cursor: url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyOCcgaGVpZ2h0PScyOCcgdmlld0JveD0nMCAwIDI4IDI4Jz48dGV4dCB4PScxNCcgeT0nMjInIGZvbnQtc2l6ZT0nMjQnIGZvbnQtZmFtaWx5PSdBcmlhbCcgZm9udC13ZWlnaHQ9J2JvbGQnIHRleHQtYW5jaG9yPSdtaWRkbGUnIGZpbGw9JyMxNWEwNGEnIHN0cm9rZT0nIzBhNWUyYycgc3Ryb2tlLXdpZHRoPScwLjYnPiQ8L3RleHQ+PC9zdmc+") 6 6, auto; }
input, textarea, button, select, [role="button"], [data-testid="stDataFrame"], [data-testid="stDataEditor"], .stTabs [data-baseweb="tab"] { cursor: auto; }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------- fetchers
def _get(url, verify=True, intentos=3, timeout=30):
    for i in range(intentos):
        try:
            r = requests.get(url, headers=HDRS, timeout=timeout, verify=verify)
            r.raise_for_status(); return r.json()
        except Exception:
            if i == intentos - 1: raise

@st.cache_data(ttl=3600, show_spinner="Tira CER…")
def cargar_cer(desde="2023-01-01"):
    cat = _get(BCRA, verify=False)["results"]
    idc = next(v["idVariable"] for v in cat if "estabilizaci" in str(v.get("descripcion", "")).lower())
    hasta = date.today().isoformat(); out = []
    for y in range(int(desde[:4]), int(hasta[:4]) + 1):
        d = desde if y == int(desde[:4]) else f"{y}-01-01"
        h = hasta if y == int(hasta[:4]) else f"{y}-12-31"
        try:
            res = _get(f"{BCRA}/{idc}?desde={d}&hasta={h}", verify=False)["results"]
            out += res[0]["detalle"] if (res and isinstance(res[0], dict) and "detalle" in res[0]) else res
        except Exception: pass
    return (pd.DataFrame(out).rename(columns={"valor": "cer"})[["fecha", "cer"]]
            .assign(fecha=lambda x: pd.to_datetime(x["fecha"]))
            .sort_values("fecha").drop_duplicates("fecha").reset_index(drop=True))

@st.cache_data(ttl=86400, show_spinner="Feriados…")
def cargar_feriados():
    fer = []
    for y in range(2016, date.today().year + 2):
        try:
            r = requests.get(f"https://api.argentinadatos.com/v1/feriados/{y}", timeout=20)
            if r.ok and r.text.strip():
                for f in r.json(): fer.append(f.get("fecha") if isinstance(f, dict) else f)
        except Exception: pass
    return np.array(sorted(set(fer)), dtype="datetime64[D]") if fer else np.array([], dtype="datetime64[D]")

@st.cache_data(ttl=3600, show_spinner="Bases CER…")
def cargar_config():
    return requests.get("https://rendimientos.co/config.json", timeout=20).json()["bonos_cer"]

@st.cache_data(ttl=3600, show_spinner="Letras…")
def cargar_letras():
    return _get("https://api.argentinadatos.com/v1/finanzas/letras")

@st.cache_data(ttl=60, show_spinner=False)
def cargar_precios():
    uni = (_get("https://data912.com/live/arg_notes") or []) + (_get("https://data912.com/live/arg_bonds") or [])
    def px(el):
        for k in ("c", "last", "close"):
            v = el.get(k)
            if isinstance(v, (int, float)) and v > 0: return float(v)
        b, a = el.get("px_bid"), el.get("px_ask")
        if isinstance(b, (int, float)) and isinstance(a, (int, float)) and b > 0 and a > 0: return (b + a) / 2
        return np.nan
    def var(el):
        for k in ("pct_change", "variation", "change_pct", "pct", "var"):
            v = el.get(k)
            if isinstance(v, (int, float)): return float(v)
        return np.nan
    def vol(el):
        for k in ("v", "volume", "q_op", "vol", "q"):
            x = el.get(k)
            if isinstance(x, (int, float)): return float(x)
        return np.nan
    precios, varia, vols = {}, {}, {}
    for el in uni:
        s = el.get("symbol")
        if s: precios[s] = px(el); varia[s] = var(el); vols[s] = vol(el)
    return precios, varia, vols, len(uni)

@st.cache_data(ttl=300, show_spinner=False)
def cargar_dolares():
    try: return _get("https://api.argentinadatos.com/v1/cotizaciones/dolares")
    except Exception: return []

@st.cache_data(ttl=300, show_spinner=False)
def cargar_global():
    import csv, io
    syms = {"5YUSY.B": "UST 5Y", "10YUSY.B": "UST 10Y", "30YUSY.B": "UST 30Y",
            "CL.F": "WTI", "CB.F": "Brent", "GC.F": "Oro", "HG.F": "Cobre", "ZS.F": "Soja"}
    out = {}
    try:
        s = ",".join(syms.keys())
        r = requests.get(f"https://stooq.com/q/l/?s={s}&f=sd2t2ohlcv&h&e=csv", timeout=20)
        for row in csv.DictReader(io.StringIO(r.text)):
            sym = str(row.get("Symbol", "")).upper()
            lbl = next((v for k, v in syms.items() if k.upper() == sym), None)
            try: val = float(row.get("Close"))
            except Exception: val = None
            if lbl and val is not None:
                out[lbl] = (val, row.get("Date"))
    except Exception:
        pass
    return out

@st.cache_data(ttl=3600, show_spinner=False)
def cargar_tasas_bcra():
    try: cat = _get(BCRA, verify=False)["results"]
    except Exception: return {}
    quiero = [("TAMAR", "tamar"), ("BADLAR priv.", "badlar"), ("Política monetaria", "política monetaria")]
    out = {}
    hoy = date.today().isoformat(); desde = (date.today() - timedelta(days=90)).isoformat()
    for nombre, kw in quiero:
        try:
            idv = next(x["idVariable"] for x in cat if kw in str(x.get("descripcion", "")).lower())
            res = _get(f"{BCRA}/{idv}?desde={desde}&hasta={hoy}", verify=False)["results"]
            serie = res[0]["detalle"] if (res and isinstance(res[0], dict) and "detalle" in res[0]) else res
            if serie:
                ult = max(serie, key=lambda x: str(x.get("fecha", "")))
                out[nombre] = (ult.get("valor"), ult.get("fecha"))
        except Exception: pass
    return out

@st.cache_data(ttl=3600, show_spinner="Tira TAMAR…")
def cargar_tamar(desde="2024-01-01"):
    try:
        cat = _get(BCRA, verify=False)["results"]
        idv = next(x["idVariable"] for x in cat if "tamar" in str(x.get("descripcion", "")).lower())
    except Exception:
        return None
    hasta = date.today().isoformat(); out = []
    for y in range(int(desde[:4]), int(hasta[:4]) + 1):
        d = desde if y == int(desde[:4]) else f"{y}-01-01"
        h = hasta if y == int(hasta[:4]) else f"{y}-12-31"
        try:
            res = _get(f"{BCRA}/{idv}?desde={d}&hasta={h}", verify=False)["results"]
            out += res[0]["detalle"] if (res and isinstance(res[0], dict) and "detalle" in res[0]) else res
        except Exception: pass
    if not out: return None
    return (pd.DataFrame(out).rename(columns={"valor": "tamar"})[["fecha", "tamar"]]
            .assign(fecha=lambda x: pd.to_datetime(x["fecha"])).sort_values("fecha")
            .drop_duplicates("fecha").set_index("fecha")["tamar"])

# ----------------------------------------------------------------- helpers comunes
def settlement(feriados, plazo):
    hoy = np.datetime64(date.today(), "D")
    t_op = np.busday_offset(hoy, 0, roll="backward", holidays=feriados)
    return pd.Timestamp(np.busday_offset(t_op, plazo, roll="forward", holidays=feriados))

def dias360(f1, f2):
    a1, m1, d1 = map(int, str(f1)[:10].split("-")); a2, m2, d2 = map(int, str(f2)[:10].split("-"))
    d1 = min(d1, 30); d2 = min(d2, 30) if d1 == 30 else d2
    return (a2 - a1) * 360 + (m2 - m1) * 30 + (d2 - d1)

def vpv_tamar(ts, feriados, emis, venc, margen, proy):
    """VPV oficial TAMAR. TAMAR = promedio simple de la tira en [emisión−10háb, vencimiento−10háb];
    parte publicada real + parte futura proyectada (proy), ponderadas por días hábiles."""
    d_e = np.datetime64(pd.Timestamp(emis).date(), "D"); d_v = np.datetime64(pd.Timestamp(venc).date(), "D")
    w0 = pd.Timestamp(np.busday_offset(d_e, -10, roll="backward", holidays=feriados))
    w1 = pd.Timestamp(np.busday_offset(d_v, -10, roll="backward", holidays=feriados))
    last = ts.index.max()
    conocido = ts[(ts.index >= w0) & (ts.index <= min(w1, last))]
    n_con = len(conocido); suma = float(conocido.sum())
    n_proj = int(np.busday_count(np.datetime64(last.date(), "D"), np.datetime64(w1.date(), "D"),
                                 holidays=feriados)) if w1 > last else 0
    total = n_con + n_proj
    if total <= 0: return None
    avg = (suma + proy * n_proj) / total                      # TAMAR promedio (%) sobre la ventana
    x = (avg + margen) / 100 / (365 / 32)
    tem = (1 + x) ** (365 / 32)
    tem = tem ** (1 / 12) - 1                                  # TAMAR TEM
    vpv = 100 * (1 + tem) ** (dias360(emis, venc) / 30)        # meses 30/360 = días/30
    return vpv, avg, tem, (n_con / total)

def regresion(df, col, excluir_fit):
    df["TIR_fit"] = np.nan; df["bps_curva"] = np.nan; df["señal"] = None
    m = df["MD"].notna() & df[col].notna() & (df["MD"] > 0) & ~df["ticker"].isin(excluir_fit)
    d = df[m]; coef = None; info = ""
    if len(d) >= 3:
        b, a = np.polyfit(np.log(d["MD"]), d[col], 1); coef = (a, b)
        df["TIR_fit"] = a + b * np.log(df["MD"])
        df["bps_curva"] = (df[col] - df["TIR_fit"]) * 10000
        df["señal"] = np.where(df["bps_curva"] >= 0, "barato", "caro")
        df.loc[df[col].isna(), "señal"] = None
        yh = a + b * np.log(d["MD"])
        r2 = 1 - ((d[col]-yh)**2).sum()/((d[col]-d[col].mean())**2).sum()
        info = f"R²={r2:.3f}"
    return df, coef, info

# ----------------------------------------------------------------- CER
def cer_ref_factory(cer_diario, feriados):
    def workday(f, n):
        return pd.Timestamp(np.busday_offset(np.datetime64(pd.Timestamp(f).date(), "D"), n, roll="backward", holidays=feriados))
    def cer_ref(f, lag=LAG): return float(cer_diario.asof(workday(f, -lag)))
    return cer_ref

def flujos_cer_base(flujos):
    cap, out = 100.0, []
    for f in sorted(flujos, key=lambda x: x["fecha"]):
        amort = float(f.get("amortizacion", 0)) * 100.0
        out.append((pd.to_datetime(f["fecha"]), cap * float(f.get("tasa_interes", 0)) * float(f.get("base", 0)) + amort))
        cap -= amort
    return out

def calc_cer(bonos_cer, precios, cer_ref, sett):
    filas = []
    for tk, b in bonos_cer.items():
        if tk in EXCLUIR_CER: continue
        coef = cer_ref(sett, LAG) / b["cer_emision"]
        cfs = [(d, c) for d, c in flujos_cer_base(b["flujos"]) if d > sett]
        if not cfs: continue
        venc = cfs[-1][0]; vt = 100 * coef; precio = precios.get(tk)
        fila = {"clase": "CER", "ticker": tk, "tipo": "0 cupón" if len(cfs) == 1 else "cupón",
                "venc": venc.date(), "dias": (venc - sett).days, "VT": round(vt, 2),
                "precio": float(precio) if precio and precio > 0 else np.nan,
                "paridad": np.nan, "TIR": np.nan, "TNA": np.nan, "MD": np.nan}
        if precio and precio > 0:
            npv = lambda r: sum(c/(1+r)**((d-sett).days/365) for d, c in cfs) - precio/coef
            try: tir = brentq(npv, -0.99, 10.0)
            except Exception: tir = np.nan
            if tir == tir:
                ts = [(d-sett).days/365 for d, _ in cfs]; pvs = [c/(1+tir)**t for t, (_, c) in zip(ts, cfs)]
                mac = sum(t*pv for t, pv in zip(ts, pvs))/sum(pvs)
                fila.update({"paridad": precio/vt, "TIR": tir,
                             "TNA": 12*((1+tir)**(1/12)-1), "MD": mac/(1+tir)})
        filas.append(fila)
    return pd.DataFrame(filas).sort_values("venc").reset_index(drop=True)

# ----------------------------------------------------------------- Tasa fija
def calc_fija(letras, precios, sett):
    filas = []
    for L in letras:
        tk = L.get("ticker")
        if not tk or tk in EXCLUIR_FIJA: continue
        vpv = pd.to_numeric(L.get("vpv"), errors="coerce"); venc = pd.to_datetime(L.get("fechaVencimiento"), errors="coerce")
        if pd.isna(vpv) or pd.isna(venc): continue
        n = (venc - sett).days
        if n <= 0: continue
        precio = precios.get(tk)
        fila = {"clase": "Tasa Fija", "ticker": tk, "tipo": {"S": "LECAP", "T": "BONCAP"}.get(tk[0], "otro"),
                "venc": venc.date(), "dias": n, "vpv": round(float(vpv), 2),
                "precio": float(precio) if precio and precio > 0 else np.nan,
                "paridad": np.nan, "TIR": np.nan, "TNA": np.nan, "TEM": np.nan, "MD": np.nan}
        if precio and precio > 0:
            r = vpv/precio; tir = r**(365/n)-1
            fila.update({"TIR": tir, "TNA": (r-1)*365/n, "TEM": (1+tir)**(1/12)-1,
                         "MD": (n/365)/(1+tir), "paridad": precio/vpv})
        filas.append(fila)
    return pd.DataFrame(filas).sort_values("venc").reset_index(drop=True)

# ----------------------------------------------------------------- carga
cer = cargar_cer(); feriados = cargar_feriados()
bonos_cer = cargar_config(); letras = list(cargar_letras())
precios_live, varia, vols, n_uni = cargar_precios()
st.session_state.setdefault("manual", {})

cer_diario = (cer.set_index("fecha")["cer"].reindex(pd.date_range(cer["fecha"].min(), cer["fecha"].max(), freq="D")).ffill())
cer_ref = cer_ref_factory(cer_diario, feriados)

# agregar nuevos CER (cupón cero) si no están en el config
for tk, (emis, venc) in NUEVOS_CER.items():
    if tk not in bonos_cer:
        bonos_cer[tk] = {"tipo": "CER", "vencimiento": venc, "cer_emision": round(cer_ref(emis, LAG), 4),
                         "flujos": [{"fecha": venc, "amortizacion": 1.0, "tasa_interes": 0.0, "base": 0.5}]}
# agregar nuevas letras fijas si no están en la API
tk_api = {L.get("ticker") for L in letras}
for tk, (emis, venc, tem) in NUEVOS_FIJA.items():
    if tk not in tk_api:
        vpv = round(100*(1+tem/100)**(dias360(emis, venc)/30), 4)
        letras.append({"ticker": tk, "fechaEmision": emis, "fechaVencimiento": venc, "tem": tem, "vpv": vpv})

# ----------------------------------------------------------------- sidebar
with st.sidebar:
    st.subheader("Parámetros")
    plazo = st.selectbox("Liquidación", [("24hs (T+1)", 1), ("CI", 0)], format_func=lambda x: x[0])[1]
    st.caption("Excluir del ajuste de curva")
    exc_fija = st.multiselect("Tasa fija", sorted({L["ticker"] for L in letras if L.get("ticker") and L["ticker"] not in EXCLUIR_FIJA}), default=[])
    exc_cer = st.multiselect("CER", sorted(k for k in bonos_cer if k not in EXCLUIR_CER), default=[t for t in ["DICP", "PARP", "TX31"] if t in bonos_cer])
    st.divider()
    auto = st.checkbox("Auto-refresco (60s)", value=True)
    cc = st.columns(2)
    if cc[0].button("Refrescar", width="stretch"): cargar_precios.clear(); st.rerun()
    if cc[1].button("Limpiar manual", width="stretch"): st.session_state.manual = {}; st.rerun()
    if st.session_state.manual:
        st.caption("Manuales: " + ", ".join(f"{k} {v:g}" for k, v in st.session_state.manual.items()))

if auto:
    try:
        from streamlit_autorefresh import st_autorefresh; st_autorefresh(interval=60_000, key="auto")
    except Exception:
        st.sidebar.caption("pip install streamlit-autorefresh")

# ----------------------------------------------------------------- cálculo
precios = {**precios_live, **st.session_state.manual}
sett = settlement(feriados, plazo)
df_cer = calc_cer(bonos_cer, precios, cer_ref, sett)
df_fija = calc_fija(letras, precios, sett)
df_cer, coef_cer, info_cer = regresion(df_cer, "TNA", exc_cer)
df_fija, coef_fija, info_fija = regresion(df_fija, "TNA", exc_fija)

def var_txt(v):
    if v is None or (isinstance(v, float) and v != v): return ""
    ind = "🟢" if v > 0 else ("🔴" if v < 0 else "⚪")
    return f"{ind} {v:+.2f}%"
for _d in (df_fija, df_cer):
    _d["var"] = _d["ticker"].map(varia)
    _d["var_txt"] = _d["var"].map(var_txt)
    _d["vol"] = _d["ticker"].map(vols)

st.title("Calculadora Esta Todo Bien Loko")
c1, c2 = st.columns(2)
c1.metric("Liquidación", sett.strftime("%d/%m/%Y"))
c2.metric(f"CER ref (−{LAG}h)", f"{cer_ref(sett, LAG):,.2f}")

tab_c, tab_f, tab_r, tab_b, tab_i, tab_m, tab_t = st.tabs(["Curvas", "Tasa Fija", "CER", "Breakeven", "Inversa", "Macro", "TAMAR"])

# ---- curva por clase ----
def curva_chart(df, coef, linecolor):
    plot = df.dropna(subset=["MD", "TNA"])
    if not len(plot):
        return None
    base = alt.Chart(plot).encode(
        x=alt.X("MD:Q", title="Modified Duration (años)", scale=alt.Scale(zero=False)),
        y=alt.Y("TNA:Q", title="TNA", axis=alt.Axis(format="%"), scale=alt.Scale(zero=False)))
    pts = base.mark_circle(size=170, opacity=.9).encode(
        color=alt.Color("señal:N", title="vs curva",
                        scale=alt.Scale(domain=["barato", "caro"], range=["#1f7a4d", "#b23a2e"])),
        tooltip=[alt.Tooltip("ticker:N", title="Especie"), alt.Tooltip("tipo:N"),
                 alt.Tooltip("venc:T", title="Vto"), alt.Tooltip("MD:Q", format=".2f"),
                 alt.Tooltip("precio:Q", format=",.2f"), alt.Tooltip("TIR:Q", format=".2%"),
                 alt.Tooltip("TNA:Q", format=".2%"), alt.Tooltip("bps_curva:Q", title="bps", format="+.0f")])
    txt = base.mark_text(dy=-12, fontSize=10, color="#555").encode(text="ticker:N")
    capas = [pts, txt]
    if coef is not None:
        a_, b_ = coef; xs = np.linspace(plot["MD"].min(), plot["MD"].max(), 60)
        ld = pd.DataFrame({"MD": xs, "TNA": a_ + b_*np.log(xs)})
        capas = [alt.Chart(ld).mark_line(color=linecolor, strokeDash=[5, 4], size=1.5).encode(x="MD:Q", y="TNA:Q")] + capas
    return alt.layer(*capas).interactive().properties(height=380)

with tab_c:
    st.markdown("**Tasa Fija** · TIR nominal")
    ch = curva_chart(df_fija, coef_fija, "#2E4A6B")
    if ch is not None: st.altair_chart(ch, width="stretch")
    st.caption(f"{info_fija}   ·   verde = barato, rojo = caro")
    st.divider()
    st.markdown("**CER** · TIR real")
    ch = curva_chart(df_cer, coef_cer, "#B5751A")
    if ch is not None: st.altair_chart(ch, width="stretch")
    st.caption(f"{info_cer}   ·   verde = barato, rojo = caro")

# ---- editor genérico ----
def editor(df, orden, cfg, key):
    edit = st.data_editor(df, column_order=orden, column_config=cfg, hide_index=True, width="stretch", height=560, key=key)
    base_px = df.set_index("ticker")["precio"]; cambios = {}
    for _, r in edit.iterrows():
        tk, v = r["ticker"], r["precio"]; old = base_px.get(tk)
        if pd.notna(v) and (pd.isna(old) or abs(float(v)-float(old)) > 1e-6): cambios[tk] = round(float(v), 4)
    if cambios: st.session_state.manual.update(cambios); st.rerun()

with tab_f:
    st.caption("Tasa fija (base 365). Editá **Precio** con doble click.")
    editor(df_fija,
        ["ticker", "venc", "precio", "var_txt", "vol", "TIR", "TNA", "TEM", "MD", "dias", "tipo", "bps_curva", "señal", "paridad", "vpv"],
        {"ticker": st.column_config.TextColumn("Especie", disabled=True),
         "venc": st.column_config.DateColumn("Vto", format="DD/MM/YYYY", disabled=True),
         "precio": st.column_config.NumberColumn("Precio", format="%.2f"),
         "var_txt": st.column_config.TextColumn("Var", disabled=True),
         "vol": st.column_config.NumberColumn("Vol", format="%d", disabled=True),
         "TIR": st.column_config.NumberColumn("TIR", format="percent", disabled=True),
         "TNA": st.column_config.NumberColumn("TNA", format="percent", disabled=True),
         "TEM": st.column_config.NumberColumn("TEM", format="percent", disabled=True),
         "MD": st.column_config.NumberColumn("MD", format="%.2f", disabled=True),
         "dias": st.column_config.NumberColumn("Días", format="%d", disabled=True),
         "tipo": st.column_config.TextColumn("Tipo", disabled=True),
         "bps_curva": st.column_config.NumberColumn("bps", format="%+.0f", disabled=True),
         "señal": st.column_config.TextColumn("Señal", disabled=True),
         "paridad": st.column_config.ProgressColumn("Paridad", format="%.1f%%", min_value=0.0, max_value=1.2),
         "vpv": st.column_config.NumberColumn("VPV", format="%.2f", disabled=True)}, "ed_fija")

with tab_r:
    st.caption("CER (TIR real, rezago 10 háb). Editá **Precio** con doble click.")
    editor(df_cer,
        ["ticker", "venc", "precio", "var_txt", "vol", "TIR", "TNA", "MD", "dias", "tipo", "bps_curva", "señal", "paridad", "VT"],
        {"ticker": st.column_config.TextColumn("Especie", disabled=True),
         "venc": st.column_config.DateColumn("Vto", format="DD/MM/YYYY", disabled=True),
         "precio": st.column_config.NumberColumn("Precio", format="%.2f"),
         "var_txt": st.column_config.TextColumn("Var", disabled=True),
         "vol": st.column_config.NumberColumn("Vol", format="%d", disabled=True),
         "TIR": st.column_config.NumberColumn("TIR real", format="percent", disabled=True),
         "TNA": st.column_config.NumberColumn("TNA real", format="percent", disabled=True),
         "MD": st.column_config.NumberColumn("MD", format="%.2f", disabled=True),
         "dias": st.column_config.NumberColumn("Días", format="%d", disabled=True),
         "tipo": st.column_config.TextColumn("Tipo", disabled=True),
         "bps_curva": st.column_config.NumberColumn("bps", format="%+.0f", disabled=True),
         "señal": st.column_config.TextColumn("Señal", disabled=True),
         "paridad": st.column_config.ProgressColumn("Paridad", format="%.1f%%", min_value=0.0, max_value=1.2),
         "VT": st.column_config.NumberColumn("VT", format="%.2f", disabled=True)}, "ed_cer")

with tab_b:
    st.markdown("**Inflación breakeven** — la inflación que pricea el mercado entre tasa fija y CER")
    fija_ok = df_fija.dropna(subset=["TIR"]).sort_values("venc").reset_index(drop=True)
    cer_ok = df_cer.dropna(subset=["TIR"]).sort_values("venc").reset_index(drop=True)
    if len(fija_ok) and len(cer_ok):
        c1, c2 = st.columns(2)
        f_tk = c1.selectbox("Bono tasa fija", fija_ok["ticker"].tolist())
        f_row = fija_ok[fija_ok["ticker"] == f_tk].iloc[0]
        # default CER = vencimiento más cercano al de la fija
        gaps = (pd.to_datetime(cer_ok["venc"]) - pd.Timestamp(f_row["venc"])).abs().dt.days
        c_list = cer_ok["ticker"].tolist(); c_def = c_list[int(gaps.values.argmin())]
        c_tk = c2.selectbox("Bono CER", c_list, index=c_list.index(c_def))
        c_row = cer_ok[cer_ok["ticker"] == c_tk].iloc[0]

        tir_f, tir_c = f_row["TIR"], c_row["TIR"]
        be = (1 + tir_f) / (1 + tir_c) - 1
        gap = abs((pd.Timestamp(f_row["venc"]) - pd.Timestamp(c_row["venc"])).days)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"{f_tk} · nominal", f"{tir_f*100:.2f}%")
        m2.metric(f"{c_tk} · real", f"{tir_c*100:.2f}%")
        m3.metric("Breakeven anual", f"{be*100:.2f}%")
        m4.metric("Breakeven mensual", f"{((1+be)**(1/12)-1)*100:.2f}%")
        if gap > 20:
            st.caption(f"⚠️ Los vencimientos difieren {gap} días — el breakeven es aproximado (lo ideal es mismo vto).")
        else:
            st.caption(f"Vencimientos alineados (gap {gap} días) — breakeven limpio.")

        ie = st.number_input("Tu inflación esperada (anual, %)", value=20.0, step=1.0)
        cer_nom = (1 + tir_c) * (1 + ie/100) - 1
        gana = f"{c_tk} (CER)" if cer_nom > tir_f else f"{f_tk} (tasa fija)"
        st.markdown(f"A **{ie:.0f}%** de inflación conviene **{gana}** "
                    f"— ventaja {abs(cer_nom-tir_f)*100:.2f} pp de TEA. "
                    f"Breakeven {be*100:.2f}%: por encima gana el CER, por debajo la fija.")

        st.divider()
        st.markdown("**Curva de inflación implícita** (TIR fija interpolada al plazo de cada CER)")
        fp = fija_ok.sort_values("dias")
        rows = []
        for _, r in cer_ok.iterrows():
            tirf_i = float(np.interp(r["dias"], fp["dias"], fp["TIR"]))
            rows.append({"CER": r["ticker"], "venc": r["venc"], "dias": int(r["dias"]),
                         "TIR_real": r["TIR"], "TIR_fija": tirf_i,
                         "breakeven": (1 + tirf_i) / (1 + r["TIR"]) - 1})
        bedf = pd.DataFrame(rows)
        ch = alt.Chart(bedf).mark_line(point=alt.OverlayMarkDef(size=80), color="#9A7B12").encode(
            x=alt.X("venc:T", title="Vencimiento"),
            y=alt.Y("breakeven:Q", title="Inflación breakeven", axis=alt.Axis(format="%"), scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("CER:N"), alt.Tooltip("venc:T", title="Vto"),
                     alt.Tooltip("breakeven:Q", title="Breakeven", format=".1%"),
                     alt.Tooltip("TIR_real:Q", format=".2%"), alt.Tooltip("TIR_fija:Q", format=".2%")])
        st.altair_chart(ch.interactive().properties(height=320), width="stretch")
        st.dataframe(bedf, hide_index=True, width="stretch",
            column_config={"CER": st.column_config.TextColumn("CER"),
                "venc": st.column_config.DateColumn("Vto", format="DD/MM/YYYY"),
                "dias": st.column_config.NumberColumn("Días", format="%d"),
                "TIR_real": st.column_config.NumberColumn("TIR real", format="percent"),
                "TIR_fija": st.column_config.NumberColumn("TIR fija (interp)", format="percent"),
                "breakeven": st.column_config.NumberColumn("Breakeven infl.", format="percent")})
        st.caption("Breakeven = (1+TIR fija)/(1+TIR real CER) − 1. La fija se interpola a cada plazo CER; "
                   "fuera del rango de la curva fija, se extrapola plano (tomar con cuidado los CER muy largos).")
    else:
        st.info("Necesito al menos un bono de cada clase con precio para calcular el breakeven.")

with tab_i:
    st.markdown("**Calculadora inversa** — ingresá una TIR objetivo y obtené el precio al que comprar")
    ops = [("Tasa Fija", t) for t in df_fija["ticker"].tolist()] + [("CER", t) for t in df_cer["ticker"].tolist()]
    if ops:
        sel = st.selectbox("Bono", ops, format_func=lambda o: f"{o[1]} · {o[0]}")
        clase, tk = sel
        tir_obj = st.number_input("TIR objetivo (TEA, %)", value=30.0, step=0.5, format="%.2f") / 100
        if clase == "Tasa Fija":
            row = df_fija[df_fija["ticker"] == tk].iloc[0]
            n = int(row["dias"]); vpv = row["vpv"]; cur = row["precio"]; cur_tir = row["TIR"]
            precio = vpv / (1 + tir_obj) ** (n/365)
            tna = (vpv/precio - 1) * 365/n
        else:
            row = df_cer[df_cer["ticker"] == tk].iloc[0]
            b = bonos_cer[tk]; coef = cer_ref(sett, LAG) / b["cer_emision"]
            cfs = [(d, c) for d, c in flujos_cer_base(b["flujos"]) if d > sett]
            n = int(row["dias"]); cur = row["precio"]; cur_tir = row["TIR"]
            precio = coef * sum(c / (1 + tir_obj) ** ((d - sett).days/365) for d, c in cfs)
            tna = 12 * ((1 + tir_obj) ** (1/12) - 1)
        tem = (1 + tir_obj) ** (1/12) - 1
        a, b2, c3, d4 = st.columns(4)
        a.metric("Precio objetivo", f"{precio:,.2f}")
        b2.metric("Precio actual", f"{cur:,.2f}" if cur == cur else "—")
        c3.metric("TNA", f"{tna*100:.2f}%")
        d4.metric("TEM", f"{tem*100:.2f}%")
        if cur == cur:
            dif = (precio/cur - 1) * 100
            st.caption(f"El precio objetivo está **{dif:+.2f}%** {'por encima' if dif > 0 else 'por debajo'} "
                       f"del actual ({cur:,.2f}, TIR {cur_tir*100:.2f}%). Si está por debajo, hoy rinde menos que tu objetivo.")
        # sensibilidad ±
        st.divider(); st.markdown("**Precio según TIR objetivo**")
        grid = []
        for dt in (-2, -1, -0.5, 0, 0.5, 1, 2):
            t = tir_obj + dt/100
            if clase == "Tasa Fija":
                p = vpv / (1 + t) ** (n/365)
            else:
                p = coef * sum(c / (1 + t) ** ((d - sett).days/365) for d, c in cfs)
            grid.append({"TIR": t, "Precio": round(p, 2)})
        gdf = pd.DataFrame(grid)
        st.dataframe(gdf, hide_index=True, width="stretch",
            column_config={"TIR": st.column_config.NumberColumn("TIR objetivo", format="percent"),
                           "Precio": st.column_config.NumberColumn("Precio", format="%.2f")})
    else:
        st.info("Sin bonos para calcular.")

with tab_m:
    st.markdown("**Macro** — referencia de mercado, para tener a mano")
    dolares = cargar_dolares()
    if dolares:
        dd = {d.get("casa"): d for d in dolares}
        st.markdown("**Dólar** · venta")
        casas = [("oficial", "Oficial"), ("mayorista", "Mayorista"), ("bolsa", "MEP"),
                 ("contadoconliqui", "CCL"), ("blue", "Blue"), ("tarjeta", "Tarjeta")]
        cols = st.columns(len(casas))
        for col, (c, lbl) in zip(cols, casas):
            d = dd.get(c)
            col.metric(lbl, f"${d['venta']:,.2f}" if d and d.get("venta") else "—")
        fechas = [str(d.get("fecha")) for d in dolares if d.get("fecha")]
        if fechas: st.caption(f"Dólar al {max(fechas)} · ArgentinaDatos")
    else:
        st.caption("No se pudo traer el dólar.")
    st.divider()
    tasas = cargar_tasas_bcra()
    if tasas:
        st.markdown("**Tasas de referencia** · % n.a.")
        cols = st.columns(max(len(tasas), 1))
        for col, (nombre, (val, fecha)) in zip(cols, tasas.items()):
            col.metric(nombre, f"{val:.2f}%" if isinstance(val, (int, float)) else "—", help=f"al {fecha}")
        st.caption("Fuente BCRA (TAMAR, BADLAR, tasa de política). Requiere IP de Argentina.")
    else:
        st.caption("Tasas BCRA no disponibles (puede requerir IP argentina).")
    st.divider()
    g = cargar_global()
    if g:
        ust = [(k, g[k]) for k in ("UST 5Y", "UST 10Y", "UST 30Y") if k in g]
        if ust:
            st.markdown("**Treasuries EE.UU.** · yield")
            cols = st.columns(len(ust))
            for col, (lbl, (val, fe)) in zip(cols, ust):
                col.metric(lbl, f"{val:.2f}%", help=f"al {fe}")
        com = [(k, g[k]) for k in ("WTI", "Brent", "Oro", "Cobre", "Soja") if k in g]
        if com:
            st.markdown("**Commodities**")
            cols = st.columns(len(com))
            for col, (lbl, (val, fe)) in zip(cols, com):
                col.metric(lbl, f"{val:,.2f}", help=f"al {fe}")
        st.caption("Treasuries y commodities: Stooq (cierre, puede tener algo de delay).")
    else:
        st.caption("Treasuries/commodities no disponibles en este momento.")

with tab_t:
    st.markdown("**Bonos TAMAR** — VPV oficial (TAMAR TEM capitalizable mensual, 30/360)")
    ts = cargar_tamar()
    if ts is None or not len(ts):
        st.info("No se pudo traer la tira TAMAR (BCRA, puede requerir IP argentina).")
    else:
        proy = float(ts.tail(5).mean()); ult_f = ts.index.max(); ult_v = float(ts.iloc[-1])
        k1, k2 = st.columns(2)
        k1.metric("TAMAR último", f"{ult_v:.2f}%", help=f"al {ult_f:%d/%m/%Y}")
        k2.metric("TAMAR proyectada", f"{proy:.2f}%", help="promedio de las últimas 5, para la parte futura")

        filas = []
        for b in SEED_TAMAR:
            tk = b["ticker"]; emis = pd.to_datetime(b["emision"]); venc = pd.to_datetime(b["vencimiento"])
            marg = float(b["margen"]); n = (venc - sett).days
            precio = precios.get(tk)
            fila = {"ticker": tk, "venc": venc.date(), "margen": marg / 100,
                    "precio": float(precio) if precio and precio > 0 else np.nan, "vol": vols.get(tk),
                    "TIR": np.nan, "TNA": np.nan, "TEM": np.nan, "MD": np.nan, "dias": n,
                    "TAMAR_prom": np.nan, "pct_conoc": np.nan, "VT_hoy": np.nan, "VPV": np.nan}
            out = vpv_tamar(ts, feriados, emis, venc, marg, proy)
            if out:
                vpv, avg, temb, pk = out
                vt_hoy = 100 * (1 + temb) ** (dias360(emis, pd.Timestamp(date.today())) / 30)
                fila.update({"TAMAR_prom": avg / 100, "pct_conoc": pk,
                             "VT_hoy": round(vt_hoy, 2), "VPV": round(vpv, 2)})
                if precio and precio > 0 and n > 0:
                    rr = vpv / precio; tir = rr ** (365 / n) - 1
                    fila.update({"TIR": tir, "TNA": (rr - 1) * 365 / n,
                                 "TEM": (1 + tir) ** (1/12) - 1, "MD": (n / 365) / (1 + tir)})
            filas.append(fila)
        res = pd.DataFrame(filas).sort_values("dias")
        st.dataframe(res, hide_index=True, width="stretch",
            column_order=["ticker", "venc", "precio", "vol", "TIR", "TNA", "TEM", "MD", "dias",
                          "margen", "TAMAR_prom", "pct_conoc", "VT_hoy", "VPV"],
            column_config={
                "ticker": st.column_config.TextColumn("Especie"),
                "venc": st.column_config.DateColumn("Vto", format="DD/MM/YYYY"),
                "precio": st.column_config.NumberColumn("Precio", format="%.2f"),
                "vol": st.column_config.NumberColumn("Vol", format="%d"),
                "TIR": st.column_config.NumberColumn("TIR", format="percent"),
                "TNA": st.column_config.NumberColumn("TNA", format="percent"),
                "TEM": st.column_config.NumberColumn("TEM", format="percent"),
                "MD": st.column_config.NumberColumn("MD", format="%.2f"),
                "dias": st.column_config.NumberColumn("Días", format="%d"),
                "margen": st.column_config.NumberColumn("Margen", format="percent"),
                "TAMAR_prom": st.column_config.NumberColumn("TAMAR prom", format="percent"),
                "pct_conoc": st.column_config.ProgressColumn("% conocido", format="%.0f%%", min_value=0.0, max_value=1.0),
                "VT_hoy": st.column_config.NumberColumn("VT hoy", format="%.2f"),
                "VPV": st.column_config.NumberColumn("VPV", format="%.2f")})

st.divider()
st.caption("⚠️ Esta herramienta es solo a modo educativo y de visualización. Los cálculos son estimaciones "
           "y pueden contener errores o supuestos que no reflejen la realidad del mercado. No constituye "
           "recomendación de inversión ni asesoramiento financiero. Verificá siempre con tu fuente oficial "
           "antes de operar.")

st.caption(f"Fuentes: BCRA CER · rendimientos.co · ArgentinaDatos · data912 ({n_uni} instrumentos).")
