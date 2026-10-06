"""El Nino -> Metro Manila heat -> Meralco generation charge.

Builds the monthly panel from the raw pulls (fetch_era5.py, fetch_stations.py,
fetch_meralco.py), runs the analysis, writes figures/ and data/processed/,
and prints every number quoted in the README.
"""
import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW, PROC, FIG = ROOT / "data" / "raw", ROOT / "data" / "processed", ROOT / "figures"
PROC.mkdir(parents=True, exist_ok=True)
FIG.mkdir(exist_ok=True)

# Palette (dataviz reference instance, light mode)
BLUE, ORANGE, AQUA, RED = "#2a78d6", "#eb6834", "#1baf7a", "#e34948"
INK, INK2, MUTED, GRID, SURFACE, NEUTRAL = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#fcfcfb", "#c9c8c2"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "lines.linewidth": 2, "legend.frameon": False,
})

STATS = {}


def heat_index_c(t_c, td_c):
    """NOAA (Rothfusz) heat index from air temperature and dew point, deg C."""
    rh = 100 * np.exp(17.625 * td_c / (243.04 + td_c)) / np.exp(17.625 * t_c / (243.04 + t_c))
    t = t_c * 9 / 5 + 32
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh - 6.83783e-3 * t ** 2
          - 5.481717e-2 * rh ** 2 + 1.22874e-3 * t ** 2 * rh + 8.5282e-4 * t * rh ** 2 - 1.99e-6 * t ** 2 * rh ** 2)
    simple = 0.5 * (t + 61 + (t - 68) * 1.2 + rh * 0.094)
    hi = np.where(t < 80, simple, hi)
    return (hi - 32) * 5 / 9


# ----------------------------------------------------------------------------- build
def build_climate():
    s = pd.read_csv(RAW / "era5_manila_samples.csv", parse_dates=["date"])
    s["month"] = s.date.dt.to_period("M")
    pm = s[s.hour_utc == 6].copy()
    pm["hi"] = heat_index_c(pm.t2m_c, pm.d2m_c)
    m = pd.DataFrame({
        "tmax_c": pm.groupby("month").t2m_c.mean(),          # 14:00 PHT
        "heat_index_c": pm.groupby("month").hi.mean(),
        "tmin_c": s[s.hour_utc == 21].groupby("month").t2m_c.mean(),  # 05:00 PHT
    })
    base = m[(m.index.year >= 2014) & (m.index.year <= 2025)]
    for c in ["tmax_c", "heat_index_c", "tmin_c"]:
        clim = base.groupby(base.index.month)[c].mean()
        m[c.replace("_c", "_clim")] = m.index.month.map(clim).values
        m[c.replace("_c", "_anom")] = m[c] - m[c.replace("_c", "_clim")]

    n = pd.read_csv(RAW / "era5_nino34_samples.csv", parse_dates=["date"])
    n = n.groupby(n.date.dt.to_period("M")).sst_c.mean().to_frame("nino34_c")
    b = n[(n.index.year >= 1991) & (n.index.year <= 2020)]
    n["nino34_anom"] = n.nino34_c - n.index.month.map(b.groupby(b.index.month).nino34_c.mean()).values
    n["oni"] = n.nino34_anom.rolling(3, center=True).mean()
    # last month has no centred window yet -> 2-month mean so the latest reading is shown
    n.loc[n.index[-1], "oni"] = n.nino34_anom.iloc[-2:].mean()
    return m.join(n, how="left")


def build_stations():
    out = {}
    for sid, name in {"RP000098429": "naia", "RP000098430": "science_garden"}.items():
        g = pd.read_csv(RAW / f"{sid}.csv", usecols=["DATE", "ELEMENT", "DATA_VALUE", "Q_FLAG"], dtype={"DATE": str})
        g = g[(g.ELEMENT == "TMAX") & g.Q_FLAG.isna()]
        g["month"] = pd.to_datetime(g.DATE).dt.to_period("M")
        g = g[g.month >= pd.Period("2014-01", "M")]
        agg = g.groupby("month").DATA_VALUE.agg(["mean", "count"])
        out[f"tmax_{name}"] = (agg["mean"] / 10).where(agg["count"] >= 15)
    return pd.DataFrame(out)


def build_panel():
    clim = build_climate()
    st = build_stations()
    r = pd.read_csv(PROC / "meralco_rates.csv")
    r.index = pd.PeriodIndex(r.pop("month"), freq="M")
    p = clim.join(st).join(r)
    p = p[p.index >= pd.Period("2014-01", "M")]
    # Meralco bills month M at supply-month (M-1) generation costs.
    for c in ["tmax_c", "tmax_clim", "tmax_anom", "heat_index_c", "heat_index_anom", "oni"]:
        p[f"{c}_supply"] = p[c].shift(1)
    # Generation charge relative to its own centred 13-month mean: strips the slow
    # fuel / FX / contract-price level so what's left is the within-year movement.
    roll = p.gen_charge.rolling(13, center=True, min_periods=7).mean()
    p["gen_dev_pct"] = 100 * (p.gen_charge / roll - 1)
    p["staggered"] = p.index.isin(pd.PeriodIndex(["2024-06", "2024-07"], freq="M")).astype(int)
    p.index.name = "month"
    p.to_csv(PROC / "monthly_panel.csv", float_format="%.4f")
    return p


# ----------------------------------------------------------------------------- helpers
def shade_el_nino(ax, p):
    on = (p.oni >= 0.5).astype(int)
    starts = p.index[(on.diff() == 1) | ((on == 1) & (np.arange(len(on)) == 0))]
    for s in starts:
        seg = on[s:]
        e = seg[seg == 0].index[0] if (seg == 0).any() else p.index[-1]
        ax.axvspan(s.to_timestamp(), e.to_timestamp(), color=ORANGE, alpha=0.09, lw=0)


def save(fig, name):
    fig.savefig(FIG / name, dpi=160, bbox_inches="tight")
    plt.close(fig)


# ----------------------------------------------------------------------------- analyses
def validate(p):
    STATS["validation"] = {}
    for col, nm in [("tmax_naia", "NAIA"), ("tmax_science_garden", "Science Garden")]:
        d = p.dropna(subset=[col])
        anom_st = d[col] - d.groupby(d.index.month)[col].transform("mean")
        anom_er = d.tmax_c - d.groupby(d.index.month).tmax_c.transform("mean")
        STATS["validation"][nm] = {
            "months": int(len(d)), "r_level": round(d[col].corr(d.tmax_c), 3),
            "r_anomaly": round(anom_st.corr(anom_er), 3), "mean_bias_c": round((d.tmax_c - d[col]).mean(), 2),
        }
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), sharey=True)
    for ax, (col, nm) in zip(axes, [("tmax_naia", "NAIA (Pasay)"), ("tmax_science_garden", "Science Garden (QC)")]):
        d = p.dropna(subset=[col])
        ax.scatter(d[col], d.tmax_c, s=22, color=BLUE, edgecolor=SURFACE, linewidth=0.8)
        lo, hi = 28, 37
        ax.plot([lo, hi], [lo, hi], color=MUTED, lw=1, ls="--")
        ax.text(hi - 0.2, hi - 0.6, "1:1", color=MUTED, fontsize=8.5, ha="right")
        st = STATS["validation"][nm.split(" (")[0]]
        ax.set_title(f"{nm}: r = {st['r_level']:.2f} (anomalies r = {st['r_anomaly']:.2f})")
        ax.set_xlabel("Station monthly mean daily max (°C)")
    axes[0].set_ylabel("ERA5 14:00 PHT, Metro Manila cells (°C)")
    fig.suptitle("ERA5 tracks the PAGASA stations month to month (2014 – Aug 2025); "
                 "it reads ~2–3°C cooler because a 25-km grid cell averages out urban heat",
                 x=0.01, y=1.03, ha="left", fontweight="bold", fontsize=10.5)
    save(fig, "fig5_era5_vs_stations.png")


def enso_to_heat(p):
    d = p.dropna(subset=["oni", "tmax_anom"])
    lags = range(0, 10)
    r_t = [d.tmax_anom.corr(d.oni.shift(k)) for k in lags]
    best = int(np.nanargmax(r_t))
    STATS["enso_heat_lag"] = {"best_lag": best, "r_tmax": round(r_t[best], 3),
                              "r_by_lag": [round(x, 3) for x in r_t]}

    # Dry-season view: Mar-May Tmax anomaly vs the preceding Nov-Jan ONI (the usual El Nino peak)
    rows = []
    for y in range(2015, 2027):
        oni = p.loc[pd.Period(f"{y - 1}-11", "M"):pd.Period(f"{y}-01", "M"), "nino34_anom"].mean()
        mam = p.loc[pd.Period(f"{y}-03", "M"):pd.Period(f"{y}-05", "M")]
        rows.append({"year": y, "oni_ndj": oni, "mam_tmax_anom": mam.tmax_anom.mean(),
                     "mam_hi_anom": mam.heat_index_anom.mean(), "mam_tmax": mam.tmax_c.mean()})
    ds = pd.DataFrame(rows)
    fit = smf.ols("mam_tmax_anom ~ oni_ndj", ds).fit()
    STATS["dry_season"] = {
        "slope_c_per_oni": round(fit.params.oni_ndj, 3), "r2": round(fit.rsquared, 3), "p": round(fit.pvalues.oni_ndj, 4),
        "table": ds.round(3).to_dict("records"),
    }
    ds.round(3).to_csv(PROC / "dry_season_vs_enso.csv", index=False)

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.9), gridspec_kw={"width_ratios": [1, 1.25]})
    ax = axes[0]
    ax.bar(list(lags), r_t, color=[BLUE if k != best else ORANGE for k in lags], width=0.6)
    ax.set_xticks(list(lags))
    ax.set_xlabel("Months by which ONI leads Manila temperature")
    ax.set_ylabel("Correlation (r)")
    ax.set_title("ENSO leads Manila heat by a few months")
    ax.annotate(f"peak r = {r_t[best]:.2f}\nat {best}-month lead", (best, r_t[best]), xytext=(best + 1.3, r_t[best] - 0.02),
                fontsize=9, color=INK2, arrowprops=dict(arrowstyle="-", color=MUTED))
    ax = axes[1]
    x = np.linspace(ds.oni_ndj.min() - 0.2, ds.oni_ndj.max() + 0.2, 50)
    ax.plot(x, fit.params.Intercept + fit.params.oni_ndj * x, color=MUTED, lw=1.2, ls="--")
    col = [ORANGE if o >= 1.5 else (BLUE if o <= -0.5 else NEUTRAL) for o in ds.oni_ndj]
    ax.scatter(ds.oni_ndj, ds.mam_tmax_anom, s=70, c=col, edgecolor=SURFACE, linewidth=1.5, zorder=3)
    for _, rw in ds.iterrows():
        ax.annotate(int(rw.year), (rw.oni_ndj, rw.mam_tmax_anom), xytext=(5, 4), textcoords="offset points",
                    fontsize=8.5, color=INK2)
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.set_xlabel("Nino-3.4 anomaly, preceding Nov–Jan (°C)")
    ax.set_ylabel("Mar–May afternoon temp anomaly (°C)")
    ax.set_title(f"Hot summers follow strong El Ninos (+{fit.params.oni_ndj:.2f}°C per 1°C, R² = {fit.rsquared:.2f})")
    save(fig, "fig2_enso_to_manila_heat.png")
    return fit


def heat_to_price(p):
    d = p.dropna(subset=["gen_dev_pct", "tmax_c_supply"]).copy()
    # Lag scan: which month's temperature lines up with the billed generation charge?
    scan = {k: d.gen_dev_pct.corr(d.tmax_c.shift(k)) for k in range(0, 4)}
    STATS["price_lag_scan"] = {k: round(v, 3) for k, v in scan.items()}

    m1 = smf.ols("gen_dev_pct ~ tmax_c_supply + staggered", d).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    m2 = smf.ols("gen_dev_pct ~ tmax_clim_supply + tmax_anom_supply + staggered", d).fit(
        cov_type="HAC", cov_kwds={"maxlags": 3})
    # Robustness: log level with calendar-year fixed effects instead of the rolling mean
    d["log_gen"] = np.log(d.gen_charge)
    d["year"] = d.index.year.astype(str)
    m3 = smf.ols("log_gen ~ tmax_c_supply + staggered + C(year)", d).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    m4 = smf.ols("gen_dev_pct ~ heat_index_c_supply + staggered", d).fit(cov_type="HAC", cov_kwds={"maxlags": 3})

    def coef(m, k, scale=1):
        lo, hi = m.conf_int().loc[k] * scale
        return {"coef": round(m.params[k] * scale, 3), "ci95": [round(lo, 3), round(hi, 3)], "p": round(m.pvalues[k], 4)}

    STATS["price_models"] = {
        "n_months": int(m1.nobs), "window": f"{d.index.min()} to {d.index.max()}",
        "m1_pct_per_c": coef(m1, "tmax_c_supply"), "m1_r2": round(m1.rsquared, 3),
        "m2_seasonal_pct_per_c": coef(m2, "tmax_clim_supply"), "m2_anomaly_pct_per_c": coef(m2, "tmax_anom_supply"),
        "m3_yearFE_pct_per_c": coef(m3, "tmax_c_supply", 100),
        "m4_pct_per_c_heat_index": coef(m4, "heat_index_c_supply"),
    }
    with open(PROC / "regression_summaries.txt", "w") as f:
        for nm, m in [("M1 deviation ~ supply-month Tmax", m1), ("M2 seasonal + anomaly", m2),
                      ("M3 log level ~ Tmax + year FE", m3), ("M4 deviation ~ heat index", m4)]:
            f.write(f"===== {nm} =====\n{m.summary()}\n\n")

    # Figure: scatter + binned means
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4), gridspec_kw={"width_ratios": [1.35, 1]})
    ax = axes[0]
    nino = d[(d.oni_supply >= 0.5)]
    other = d[(d.oni_supply < 0.5)]
    ax.scatter(other.tmax_c_supply, other.gen_dev_pct, s=30, color=BLUE, edgecolor=SURFACE, lw=0.8,
               label="Other months", zorder=3)
    ax.scatter(nino.tmax_c_supply, nino.gen_dev_pct, s=34, color=ORANGE, edgecolor=SURFACE, lw=0.8,
               label="El Nino months (ONI ≥ 0.5)", zorder=3)
    x = np.linspace(d.tmax_c_supply.min(), d.tmax_c_supply.max(), 50)
    ax.plot(x, m1.params.Intercept + m1.params.tmax_c_supply * x, color=INK2, lw=1.5)
    for per, lab in [("2024-05", "May '24 bill\n(Apr heat, red alerts)"), ("2024-06", "Jun '24: ERC\nstaggered WESM")]:
        r = d.loc[pd.Period(per, "M")]
        ax.annotate(lab, (r.tmax_c_supply, r.gen_dev_pct), xytext=(-95 if per == "2024-06" else -110, 8),
                    textcoords="offset points", fontsize=8, color=INK2, arrowprops=dict(arrowstyle="-", color=MUTED))
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.set_xlabel("Supply-month afternoon temperature, Metro Manila (°C)")
    ax.set_ylabel("Generation charge vs 13-mo avg (%)")
    lo, hi = m1.conf_int().loc["tmax_c_supply"]
    ax.set_title(f"Month to month the link is weak: {m1.params.tmax_c_supply:+.1f}% per °C (95% CI {lo:+.1f} to {hi:+.1f})",
                 fontsize=10)
    ax.legend(loc="upper left", fontsize=8.5)

    ax = axes[1]
    bins = [26, 29, 30, 31, 32, 36]
    d["bin"] = pd.cut(d.tmax_c_supply, bins)
    g = d[d.staggered == 0].groupby("bin", observed=True).gen_dev_pct.agg(["mean", "count"])
    labels = ["<29", "29–30", "30–31", "31–32", "≥32"][:len(g)]
    ax.bar(labels, g["mean"], color=[BLUE if v < 0 else ORANGE for v in g["mean"]], width=0.62)
    for i, (v, n) in enumerate(zip(g["mean"], g["count"])):
        ax.text(i, v + (0.12 if v >= 0 else -0.12), f"{v:+.1f}%\nn={n}", ha="center", va="bottom" if v >= 0 else "top",
                fontsize=8, color=INK2)
    ax.margins(y=0.25)
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.set_xlabel("Supply-month afternoon temp (°C)")
    ax.set_ylabel("Avg generation charge vs 13-mo avg (%)")
    ax.set_title("But binned, hot months do bill higher")
    STATS["price_bins"] = {lab: {"mean_pct": round(v, 2), "n": int(n)} for lab, v, n in zip(labels, g["mean"], g["count"])}
    save(fig, "fig3_heat_vs_generation_charge.png")

    # Seasonal profile by billing month
    s = d[d.staggered == 0].groupby(d[d.staggered == 0].index.month).agg(
        dev=("gen_dev_pct", "mean"), t=("tmax_c_supply", "mean"))
    STATS["seasonal_profile"] = s.round(2).to_dict()
    from scipy import stats as sps
    dd = d[d.staggered == 0]
    summer = dd[dd.index.month.isin([4, 5, 6, 7])].gen_dev_pct
    rest = dd[~dd.index.month.isin([4, 5, 6, 7])].gen_dev_pct
    t = sps.ttest_ind(summer, rest, equal_var=False)
    STATS["summer_premium"] = {"apr_jul_bill_mean_pct": round(summer.mean(), 2), "other_mean_pct": round(rest.mean(), 2),
                               "diff_pp": round(summer.mean() - rest.mean(), 2), "welch_p": round(t.pvalue, 4),
                               "n_summer": int(len(summer)), "n_other": int(len(rest)),
                               "share_summer_positive": round((summer > 0).mean(), 2)}
    fig, axes = plt.subplots(2, 1, figsize=(8.5, 5), sharex=True, gridspec_kw={"height_ratios": [1, 1.3]})
    mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    axes[0].plot(s.index, s.t, color=ORANGE, marker="o", ms=5)
    axes[0].set_ylabel("°C")
    axes[0].set_title("Supply-month afternoon temperature (the month before the bill)")
    axes[1].bar(s.index, s.dev, color=[BLUE if v < 0 else ORANGE for v in s.dev], width=0.6)
    axes[1].axhline(0, color=MUTED, lw=0.8)
    axes[1].set_ylabel("% vs 13-mo avg")
    axes[1].set_title("Generation charge by billing month (avg 2018–2026)")
    axes[1].set_xticks(range(1, 13), mon, fontsize=9)
    axes[1].set_xlabel("Billing month (temperature panel shows the preceding supply month)")
    fig.tight_layout()
    save(fig, "fig4_seasonal_profile.png")
    return m1, m2


def timeline(p):
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True, gridspec_kw={"height_ratios": [1, 1, 1.2]})
    t = p.index.to_timestamp()
    ax = axes[0]
    col = [ORANGE if v >= 0.5 else (BLUE if v <= -0.5 else NEUTRAL) for v in p.oni.fillna(0)]
    ax.bar(t, p.oni, width=25, color=col)
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.set_ylabel("°C")
    ax.set_title("ENSO: ONI-style Nino-3.4 index (ERA5 SST, 1991–2020 base). Orange = El Nino, blue = La Nina")
    for per, lab in [("2015-11", "2015–16\nSuper El Nino"), ("2023-12", "2023–24\nstrong El Nino")]:
        v = p.loc[pd.Period(per, "M"), "oni"]
        ax.annotate(f"{lab}\npeak {v:+.1f}", (pd.Period(per, "M").to_timestamp(), v), xytext=(22, -4),
                    textcoords="offset points", fontsize=8.5, color=INK2, va="top")
    last = p.oni.dropna().index[-1]
    ax.annotate(f"2026 event building\n{last.strftime('%b %Y')}: {p.oni.dropna().iloc[-1]:+.1f}",
                (last.to_timestamp(), p.oni.dropna().iloc[-1]), xytext=(-120, -8), textcoords="offset points",
                fontsize=8.5, color=INK2, va="top")

    ax = axes[1]
    a = p.tmax_anom
    ax.bar(t, a, width=25, color=[RED if v > 0 else BLUE for v in a.fillna(0)])
    ax.plot(t, a.rolling(6, center=True).mean(), color=INK, lw=1.4)
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.set_ylabel("°C")
    ax.set_title("Metro Manila afternoon temperature anomaly (ERA5, 14:00 PHT); line = 6-month mean")

    ax = axes[2]
    shade_el_nino(ax, p)
    ax.plot(t, p.gen_charge, color=BLUE, marker="o", ms=2.5)
    ax.set_ylabel("₱/kWh")
    ax.set_title("Meralco generation charge, residential 101–200 kWh (official rate schedules)")
    for per, lab, dy in [("2022-07", "2022 coal/LNG\nprice shock", 18), ("2024-06", "Jun 2024: ERC staggers\nApr–May WESM spike", -30),
                         ("2026-09", "2026 oil crisis\n+ weak peso", 10)]:
        v = p.loc[pd.Period(per, "M"), "gen_charge"]
        ax.annotate(lab, (pd.Period(per, "M").to_timestamp(), v), xytext=(-60 if per != "2026-09" else -110, dy),
                    textcoords="offset points", fontsize=8.5, color=INK2, arrowprops=dict(arrowstyle="-", color=MUTED))
    ax.text(0.01, 0.92, "Shaded = El Nino conditions (ONI ≥ 0.5)", transform=ax.transAxes, fontsize=8.5, color=INK2)
    ax.text(0.085, 0.45, "Official rate-schedule PDFs\nstart Oct 2018", transform=ax.transAxes, fontsize=8.5,
            color=MUTED, ha="center")
    ax.set_xlim(pd.Timestamp("2014-01-01"), pd.Timestamp("2026-11-01"))
    fig.tight_layout()
    save(fig, "fig1_timeline.png")


def outlook(p, enso_fit):
    """2027 dry-season scenarios for the 2026-27 event (bills Apr-Jun 2027)."""
    base = p.loc[pd.Period("2026-09", "M"), "gen_charge"]
    prem = STATS["summer_premium"]["apr_jul_bill_mean_pct"]
    may24 = 100 * (p.loc[pd.Period("2024-05", "M"), "gen_charge"] / p.loc[pd.Period("2024-04", "M"), "gen_charge"] - 1)
    heat = {o: round(enso_fit.params.Intercept + enso_fit.params.oni_ndj * o, 2) for o in (2.0, 2.5, 3.0)}
    rows = [
        {"scenario": "A. Normal summer (seasonal premium only)", "gen_pct": prem},
        {"scenario": "B. 2024-style tight grid (May-2024 bill jump)", "gen_pct": may24},
        {"scenario": "C. B on top of A", "gen_pct": prem + may24},
    ]
    for r in rows:
        r["php_per_kwh"] = round(base * r["gen_pct"] / 100, 2)
        r["php_per_200kwh_incl_vat"] = round(base * r["gen_pct"] / 100 * 200 * 1.12)
        r["gen_pct"] = round(r["gen_pct"], 1)
    o = pd.DataFrame(rows)
    o.to_csv(PROC / "outlook_2027_dry_season.csv", index=False)
    STATS["outlook"] = {"base_gen_charge_sep2026": base, "may2024_mom_pct": round(may24, 2),
                        "expected_mam2027_tmax_anom_by_peak_oni": heat, "scenarios": rows}


def six_month_outlook(p, oni_oct2026=3.0):
    """PAGASA window: very strong El Nino Oct 2026 - Mar 2027 -> Nov 2026 - Apr 2027 bills.

    Temperature: cool-season (Oct-Mar) regression of the Manila afternoon anomaly on
    ONI five months earlier, so Oct 2026 - Feb 2027 use ONI already observed and only
    Mar 2027 needs an assumed Oct 2026 value.
    Price: (a) heat channel = supply-month temperature model x forecast anomaly;
    (b) analog channel = Nov-Apr bill deviation regressed on supply-season ONI.
    """
    base = p.loc[pd.Period("2026-09", "M"), "gen_charge"]
    oni = p.oni.copy()
    oni[pd.Period("2026-10", "M")] = oni_oct2026
    q = p.assign(oni_l5=p.oni.shift(5))
    cool = q[q.index.month.isin([10, 11, 12, 1, 2, 3])].dropna(subset=["tmax_anom", "oni_l5"])
    ft = smf.ols("tmax_anom ~ oni_l5", cool).fit()
    fh = smf.ols("heat_index_anom ~ oni_l5", cool).fit()

    d = p.dropna(subset=["gen_dev_pct"])
    d = d[(d.staggered == 0) & d.index.month.isin([11, 12, 1, 2, 3, 4])]
    season = pd.Series([i.year if i.month >= 11 else i.year - 1 for i in d.index], index=d.index)
    seas = pd.DataFrame({"dev": d.gen_dev_pct.groupby(season).mean(), "oni": d.oni_supply.groupby(season).mean()})
    fs = smf.ols("dev ~ oni", seas).fit()
    normal = d.groupby(d.index.month).gen_dev_pct.mean()
    m1_slope = STATS["price_models"]["m1_pct_per_c"]["coef"]
    peak = oni_oct2026
    analog_pct = fs.params.oni * peak  # vs an ENSO-neutral season

    rows = []
    for wm in pd.period_range("2026-10", "2027-03", freq="M"):
        o = oni[wm - 5]
        pr = ft.get_prediction(pd.DataFrame({"oni_l5": [o]})).summary_frame(alpha=0.2)
        ta = float(pr["mean"].iloc[0])
        heat_pct = m1_slope * ta
        rows.append({
            "weather_month": str(wm), "bill_month": str(wm + 1), "oni_5mo_earlier": round(o, 2),
            "tmax_anom_c": round(ta, 2), "tmax_anom_80pct": [round(float(pr.obs_ci_lower.iloc[0]), 2),
                                                             round(float(pr.obs_ci_upper.iloc[0]), 2)],
            "heat_index_anom_c": round(fh.params.Intercept + fh.params.oni_l5 * o, 2),
            "normal_bill_dev_pct": round(normal[(wm + 1).month], 2),
            "heat_channel_php_kwh": round(base * heat_pct / 100, 3),
            "heat_channel_php_200kwh": round(base * heat_pct / 100 * 224),
        })
    out = pd.DataFrame(rows)
    out.to_csv(PROC / "outlook_next_6_months.csv", index=False)
    STATS["six_month_outlook"] = {
        "temp_model": {"slope_c_per_oni": round(ft.params.oni_l5, 3), "r2": round(ft.rsquared, 3),
                       "p": round(ft.pvalues.oni_l5, 4), "n": int(ft.nobs)},
        "hi_model": {"slope_c_per_oni": round(fh.params.oni_l5, 3), "p": round(fh.pvalues.oni_l5, 4)},
        "analogs_oct_mar_tmax_anom": {a: round(p.loc[a:b].tmax_anom.mean(), 2) for a, b in
                                      [("2015-10", "2016-03"), ("2023-10", "2024-03"), ("2018-10", "2019-03")]},
        "season_price_model": {"pct_per_oni": round(fs.params.oni, 2), "p": round(fs.pvalues.oni, 3),
                               "r2": round(fs.rsquared, 2), "n_seasons": int(fs.nobs),
                               "seasons": seas.round(2).reset_index(names="season").to_dict("records")},
        "analog_add_at_peak": {"peak_oni": peak, "pct": round(analog_pct, 2),
                               "php_kwh": round(base * analog_pct / 100, 3),
                               "php_200kwh_incl_vat": round(base * analog_pct / 100 * 224)},
        "rows": rows,
    }


def event_table(p):
    """Billing-month Apr-Jun generation charge vs its 13-mo average, by year."""
    rows = []
    for y in range(2019, 2027):
        sl = p.loc[pd.Period(f"{y}-04", "M"):pd.Period(f"{y}-06", "M")]
        sup = p.loc[pd.Period(f"{y}-03", "M"):pd.Period(f"{y}-05", "M")]
        rows.append({"year": y, "nino34_ndj": round(p.loc[pd.Period(f"{y - 1}-11", "M"):pd.Period(f"{y}-01", "M"),
                                                       "nino34_anom"].mean(), 2),
                     "mar_may_tmax_anom": round(sup.tmax_anom.mean(), 2),
                     "apr_jun_bill_gen_dev_pct": round(sl.gen_dev_pct.mean(), 2),
                     "apr_jun_bill_gen_charge": round(sl.gen_charge.mean(), 3)})
    STATS["dry_season_bills"] = rows
    pd.DataFrame(rows).to_csv(PROC / "dry_season_bills.csv", index=False)


if __name__ == "__main__":
    p = build_panel()
    validate(p)
    timeline(p)
    enso_fit = enso_to_heat(p)
    m1, m2 = heat_to_price(p)
    event_table(p)
    outlook(p, enso_fit)
    six_month_outlook(p)
    oni = p.oni.dropna()
    STATS["oni_peaks"] = {"2015_16": round(p.loc["2015-06":"2016-06"].oni.max(), 2),
                          "2023_24": round(p.loc["2023-06":"2024-06"].oni.max(), 2),
                          "latest": [str(oni.index[-1]), round(oni.iloc[-1], 2)],
                          "last_6": {str(k): round(v, 2) for k, v in oni.iloc[-6:].items()}}
    STATS["hottest_months"] = {str(k): round(v, 2) for k, v in p.tmax_c.nlargest(6).items()}
    (PROC / "stats.json").write_text(json.dumps(STATS, indent=2, default=str))
    print(json.dumps(STATS, indent=2, default=str))
