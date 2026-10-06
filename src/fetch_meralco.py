"""Download Meralco's monthly Summary Schedule of Rates PDFs and extract the
residential (101-200 kWh) generation charge.

Meralco publishes each month's schedule at a predictable public S3 path:
  https://meralcomain.s3.ap-southeast-1.amazonaws.com/YYYY-MM/MM-YYYY_rate_schedule.pdf
Files are available from Oct 2018 onward (earlier ones return 403).

A few months are missing from the bucket; those are filled from Meralco's own
press releases (see MANUAL below) and flagged in the `source` column.

Output: data/processed/meralco_rates.csv
"""
import concurrent.futures as cf
import pathlib
import re
import urllib.request

import pandas as pd
import pypdf

ROOT = pathlib.Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "data" / "raw" / "meralco_rate_schedules"
S3 = "https://meralcomain.s3.ap-southeast-1.amazonaws.com"

# Generation charge (PHP/kWh) for months whose schedule PDF is not in the bucket.
MANUAL_GEN = {
    # June 2024 billing: ERC ordered May-2024 WESM costs staggered over 4 months;
    # Meralco: generation charge down P1.8308 from May's P6.8344 (July: +P2.0021 -> 7.0057, consistent).
    "2024-06": (5.0036, "Meralco June 2024 rates update (May 6.8344 - 1.8308)"),
    # Meralco January 2024 rates update: "up from P6.5332 per kWh in December".
    "2023-12": (6.5332, "Meralco January 2024 rates update"),
}

# Overall rate for a typical 200 kWh household (PHP/kWh, all-in incl. VAT).
# Official Meralco announcements where we verified them; otherwise the pinas.solar
# compilation of Meralco advisories (source='compiled'), which we found to be wrong
# in some months (e.g. May/Jun/Sep 2024) -> treat as indicative only.
OVERALL_OFFICIAL = {
    "2023-01": 10.9001, "2023-10": 11.8198, "2023-11": 12.0545,
    "2024-01": 11.3221, "2024-03": 11.9397, "2024-04": 10.9518, "2024-05": 11.4139,
    "2024-06": 9.4516, "2024-07": 11.6012, "2024-08": 11.6339, "2024-09": 11.7882,
    "2024-10": 11.4295, "2024-11": 11.8569, "2024-12": 11.9617,
    "2026-01": 12.9508, "2026-02": 13.1734, "2026-05": 14.3345, "2026-06": 14.4833,
    "2026-07": 14.8261, "2026-08": 14.7833, "2026-09": 14.7424,
}
OVERALL_COMPILED = {
    2018: [9.2487, 9.2455, 9.6800, 10.3347, 10.0033, 9.8789, 10.1925, 10.2190, 10.0732, 9.9766, 10.1503, 10.2190],
    2019: [9.8385, 10.4067, 10.4961, 10.5582, 10.5550, 10.0918, 9.9850, 9.5674, 9.1241, 9.0862, 9.5579, 9.8622],
    2020: [9.4523, 8.8623, 8.8901, 8.9951, 8.7468, 8.7251, 7.6932, 7.8907, 8.4288, 8.2721, 8.2721, 8.0750],
    2021: [8.7497, 8.6793, 8.3195, 8.4067, 8.5920, 8.6718, 8.9071, 9.0036, 9.1091, 9.1386, 9.4630, 9.7773],
    2022: [9.7027, 9.7545, 10.1065, 10.1830, 10.5152, 10.4612, 9.7545, 9.5458, 9.9365, 9.8623, 9.9472, 10.2769],
    2023: [10.9001, 10.8895, 11.4348, 11.3168, 11.4929, 11.9112, 11.1899, 10.8991, 11.3997, 11.8198, 12.0545, 11.2584],
    2024: [11.3221, 11.8482, 11.9397, 10.9518, 11.3544, 10.6036, 11.6012, 11.6339, 11.7631, 11.4295, 11.8569, 11.9617],
    2025: [11.7442, 11.9772, 12.2261, 13.0127, 12.2183, 12.1552, 12.6435, 13.2703, 13.0851, 13.3182, 13.4702, 13.1145],
    2026: [12.9508, 13.1734, 13.8161, 14.3496, 14.3345, None, 14.8261],
}


def download(start="2018-10", end="2026-12"):
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    def get(p):
        dst = PDF_DIR / f"{p.strftime('%Y-%m')}.pdf"
        if dst.exists():
            return
        try:
            data = urllib.request.urlopen(f"{S3}/{p.strftime('%Y-%m')}/{p.strftime('%m-%Y')}_rate_schedule.pdf", timeout=30).read()
            dst.write_bytes(data)
        except Exception:
            pass  # not published (403)

    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(get, pd.period_range(start, end, freq="M")))


def generation_charge(pdf):
    text = pypdf.PdfReader(pdf).pages[0].extract_text()
    row = next(l for l in text.splitlines() if re.match(r"\s*101\s*TO\s*200", l))
    return float(row.split()[4])  # "101 TO 200 KWH <generation> ..."


def build():
    rows = [{"month": p.stem, "gen_charge": generation_charge(p), "gen_source": "rate_schedule_pdf"}
            for p in sorted(PDF_DIR.glob("*.pdf"))]
    rows += [{"month": m, "gen_charge": v, "gen_source": src} for m, (v, src) in MANUAL_GEN.items()]
    df = pd.DataFrame(rows).sort_values("month").set_index("month")

    overall = {f"{y}-{i + 1:02d}": v for y, vals in OVERALL_COMPILED.items() for i, v in enumerate(vals) if v}
    df["overall_rate"] = pd.Series(overall)
    df["overall_source"] = "compiled"
    off = pd.Series(OVERALL_OFFICIAL)
    df.loc[df.index.isin(off.index), "overall_rate"] = off
    df.loc[df.index.isin(off.index), "overall_source"] = "meralco_official"
    df.loc[df.overall_rate.isna(), "overall_source"] = None
    out = ROOT / "data" / "processed" / "meralco_rates.csv"
    df.to_csv(out)
    print(df.tail(15))
    print("months:", len(df), "->", out)


if __name__ == "__main__":
    download()
    build()
