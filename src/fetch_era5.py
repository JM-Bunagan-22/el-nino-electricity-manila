"""Fetch ERA5 reanalysis for Metro Manila and the Nino-3.4 box from Google's public
ARCO-ERA5 Zarr store (gs://gcp-public-data-arco-era5), no credentials needed.

Each Zarr chunk is one global hourly field (721 x 1440, blosc/lz4 float32), so we
sample hours instead of pulling the full archive:
  * Metro Manila 2 m temperature at 06 UTC (14:00 PHT, near the daily max) and
    21 UTC (05:00 PHT, near the daily min), plus 06 UTC dew point -> every 2nd day.
  * Sea-surface temperature at 12 UTC every 5th day over the Nino-3.4 box
    (5N-5S, 170W-120W) from 1991 so a 1991-2020 climatology can be built.

Outputs (data/raw/):
  era5_manila_samples.csv   date, hour_utc, t2m_c, d2m_c
  era5_nino34_samples.csv   date, sst_c
"""
import concurrent.futures as cf
import datetime as dt
import pathlib
import urllib.request

import blosc
import numpy as np
import pandas as pd

BASE = "https://storage.googleapis.com/gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
T0 = dt.datetime(1900, 1, 1)  # Zarr time axis origin (data start 1940)
RAW = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"

# Metro Manila land cells on the 0.25 deg grid: (14.50N,121.00E) Pasay/Makati,
# (14.75N,121.00E) Caloocan/QC, (14.75N,121.25E) Marikina/east QC.
MANILA_CELLS = [(14.50, 121.00), (14.75, 121.00), (14.75, 121.25)]


def lat_idx(lat):
    return int(round((90 - lat) / 0.25))


def lon_idx(lon):
    return int(round((lon % 360) / 0.25))


def read_field(var, when):
    i = int((when - T0).total_seconds() // 3600)
    for attempt in range(4):
        try:
            raw = urllib.request.urlopen(f"{BASE}/{var}/{i}.0.0", timeout=60).read()
            return np.frombuffer(blosc.decompress(raw), dtype="<f4").reshape(721, 1440)
        except Exception:
            if attempt == 3:
                raise


def manila_sample(when):
    t = read_field("2m_temperature", when)
    t2m = np.mean([t[lat_idx(a), lon_idx(o)] for a, o in MANILA_CELLS]) - 273.15
    d2m = np.nan
    if when.hour == 6:
        d = read_field("2m_dewpoint_temperature", when)
        d2m = np.mean([d[lat_idx(a), lon_idx(o)] for a, o in MANILA_CELLS]) - 273.15
    return when.date(), when.hour, round(float(t2m), 3), round(float(d2m), 3)


def nino34_sample(when):
    s = read_field("sea_surface_temperature", when)
    box = s[lat_idx(5):lat_idx(-5) + 1, lon_idx(-170):lon_idx(-120) + 1]
    w = np.cos(np.deg2rad(np.linspace(5, -5, box.shape[0])))[:, None] * np.ones_like(box)
    ok = np.isfinite(box)
    return when.date(), round(float((box[ok] * w[ok]).sum() / w[ok].sum() - 273.15), 4)


def main(end=dt.date(2026, 9, 30)):
    RAW.mkdir(parents=True, exist_ok=True)
    days = pd.date_range("2014-01-01", end, freq="2D")
    jobs = [dt.datetime(d.year, d.month, d.day, h) for d in days for h in (6, 21)]
    with cf.ThreadPoolExecutor(24) as ex:
        rows = list(ex.map(manila_sample, jobs))
    pd.DataFrame(rows, columns=["date", "hour_utc", "t2m_c", "d2m_c"]).to_csv(
        RAW / "era5_manila_samples.csv", index=False)
    print("manila samples:", len(rows))

    sst_days = pd.date_range("1991-01-01", end, freq="5D")
    with cf.ThreadPoolExecutor(24) as ex:
        rows = list(ex.map(nino34_sample, [dt.datetime(d.year, d.month, d.day, 12) for d in sst_days]))
    pd.DataFrame(rows, columns=["date", "sst_c"]).to_csv(RAW / "era5_nino34_samples.csv", index=False)
    print("nino3.4 samples:", len(rows))


if __name__ == "__main__":
    main()
