"""Download GHCN-Daily records for the two PAGASA synoptic stations in Metro Manila
(NOAA open-data mirror on AWS) - used to validate the ERA5 temperatures.

  RP000098429  NAIA (Ninoy Aquino Int'l Airport), Pasay
  RP000098430  Science Garden, Quezon City

Note: the public mirror currently ends on 2025-08-24.
"""
import pathlib
import urllib.request

RAW = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"
STATIONS = {"RP000098429": "NAIA", "RP000098430": "Science Garden"}

if __name__ == "__main__":
    RAW.mkdir(parents=True, exist_ok=True)
    for sid in STATIONS:
        url = f"https://noaa-ghcn-pds.s3.amazonaws.com/csv/by_station/{sid}.csv"
        (RAW / f"{sid}.csv").write_bytes(urllib.request.urlopen(url, timeout=120).read())
        print("saved", sid)
