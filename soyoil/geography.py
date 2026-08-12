"""Reference geography: demo counties across the US soybean belt.

Each region carries the fields needed by the phenology and weather layers:
centroid lat/lon, typical maturity group, typical planting window, soil
available-water capacity (mm, top 1 m — SSURGO-typical values), and monthly
climate normals (1991–2020-like) used by the offline weather generator and
for extending in-season weather with climatology.

Normals are (tmax_c, tmin_c, precip_mm_month, srad_MJ_m2_day) for Jan..Dec,
consistent with published NOAA/PRISM county normals for these areas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Region:
    region_id: str
    name: str
    state: str
    fips: str
    lat: float
    lon: float
    maturity_group: float
    typical_planting_doy: int          # day-of-year of median planting
    soil_awc_mm: float                 # plant-available water, top 1 m
    som_pct: float                     # soil organic matter %
    normals: tuple[tuple[float, float, float, float], ...]  # 12 months

    def typical_planting(self, year: int) -> date:
        return date.fromordinal(date(year, 1, 1).toordinal() + self.typical_planting_doy - 1)


# Monthly normals: (tmax, tmin, precip_mm, srad). Values are representative
# of PRISM/NOAA normals for each county; the offline generator perturbs them.
REGIONS: dict[str, Region] = {}


def _add(r: Region) -> None:
    REGIONS[r.region_id] = r


_add(Region(
    "story-ia", "Story County", "IA", "19169", 42.04, -93.46, 2.6, 130, 190.0, 4.5,
    ((-3.2, -13.0, 20, 6.5), (-0.5, -10.6, 26, 9.6), (7.2, -3.9, 52, 13.0),
     (15.4, 2.2, 92, 16.8), (21.6, 8.9, 118, 20.4), (26.7, 14.7, 133, 22.6),
     (28.6, 16.8, 114, 22.4), (27.4, 15.5, 117, 19.6), (24.2, 10.4, 84, 15.6),
     (16.6, 3.4, 66, 11.0), (7.4, -3.2, 43, 7.0), (-0.9, -9.8, 29, 5.6)),
))
_add(Region(
    "champaign-il", "Champaign County", "IL", "17019", 40.14, -88.20, 3.4, 125, 200.0, 3.8,
    ((0.6, -8.3, 51, 6.8), (3.2, -6.2, 51, 9.8), (10.3, -0.6, 71, 13.2),
     (17.7, 5.3, 95, 17.0), (23.4, 11.4, 121, 20.6), (28.2, 16.8, 110, 22.8),
     (29.6, 18.6, 116, 22.2), (28.8, 17.5, 96, 19.8), (25.7, 12.9, 79, 16.0),
     (18.8, 6.2, 83, 11.4), (10.5, 0.1, 76, 7.2), (3.1, -5.6, 62, 5.8)),
))
_add(Region(
    "cass-nd", "Cass County", "ND", "38017", 46.93, -97.25, 0.4, 140, 160.0, 5.2,
    ((-9.3, -19.6, 15, 5.6), (-5.9, -16.6, 15, 8.8), (1.8, -8.8, 28, 12.6),
     (11.9, -0.6, 43, 16.4), (19.6, 6.6, 76, 20.0), (24.6, 12.4, 102, 21.8),
     (27.4, 14.9, 84, 22.2), (26.6, 13.5, 68, 18.8), (21.6, 8.0, 62, 14.0),
     (12.8, 0.6, 51, 9.2), (2.1, -8.0, 24, 5.8), (-6.9, -16.5, 17, 4.6)),
))
_add(Region(
    "brookings-sd", "Brookings County", "SD", "46011", 44.37, -96.79, 1.6, 135, 170.0, 4.8,
    ((-4.9, -16.2, 12, 6.0), (-1.9, -13.4, 15, 9.2), (5.4, -6.4, 34, 12.8),
     (14.2, 0.6, 62, 16.6), (21.1, 7.7, 87, 20.2), (26.2, 13.6, 110, 22.2),
     (28.8, 15.8, 84, 22.6), (27.6, 14.4, 76, 19.4), (23.4, 8.9, 71, 15.0),
     (15.2, 1.4, 54, 10.4), (5.1, -6.8, 26, 6.4), (-2.9, -13.2, 15, 5.2)),
))
_add(Region(
    "tippecanoe-in", "Tippecanoe County", "IN", "18157", 40.39, -86.89, 3.2, 127, 195.0, 3.6,
    ((0.9, -7.7, 55, 6.4), (3.4, -6.0, 52, 9.4), (10.4, -0.6, 74, 12.8),
     (17.7, 5.1, 96, 16.6), (23.3, 11.2, 121, 20.2), (28.1, 16.4, 111, 22.4),
     (29.4, 18.2, 112, 22.0), (28.7, 17.2, 92, 19.4), (25.6, 12.6, 79, 15.8),
     (18.7, 6.1, 82, 11.0), (10.4, 0.2, 78, 6.8), (3.4, -5.2, 64, 5.4)),
))
_add(Region(
    "saline-ne", "Saline County", "NE", "31151", 40.52, -97.14, 2.9, 128, 180.0, 3.2,
    ((2.2, -9.9, 15, 7.4), (5.0, -7.7, 22, 10.2), (12.3, -1.7, 51, 13.8),
     (18.9, 4.3, 76, 17.4), (24.4, 11.0, 118, 20.8), (30.1, 16.9, 108, 23.2),
     (32.3, 19.3, 89, 23.0), (31.1, 17.9, 92, 20.2), (27.3, 12.4, 76, 16.2),
     (19.8, 5.3, 62, 11.8), (11.1, -1.9, 33, 7.8), (3.4, -7.7, 22, 6.4)),
))
_add(Region(
    "blue-earth-mn", "Blue Earth County", "MN", "27013", 44.03, -94.07, 1.9, 133, 175.0, 5.0,
    ((-5.6, -15.7, 19, 5.8), (-2.4, -12.9, 22, 8.8), (4.9, -6.1, 42, 12.4),
     (13.8, 0.9, 76, 16.2), (20.9, 8.1, 98, 19.8), (26.1, 14.1, 122, 21.8),
     (28.3, 16.3, 102, 21.8), (26.9, 14.8, 98, 18.8), (22.9, 9.6, 82, 14.6),
     (14.9, 2.4, 66, 10.0), (5.2, -5.4, 36, 6.2), (-3.1, -12.6, 25, 4.8)),
))
_add(Region(
    "shelby-oh", "Shelby County", "OH", "39149", 40.33, -84.20, 3.1, 129, 185.0, 3.4,
    ((0.4, -7.6, 60, 5.8), (2.4, -6.6, 54, 8.6), (9.1, -1.6, 72, 12.2),
     (16.4, 4.1, 92, 16.2), (22.4, 10.2, 112, 19.8), (27.2, 15.6, 106, 21.8),
     (28.7, 17.3, 104, 21.4), (28.0, 16.4, 88, 18.8), (24.9, 12.1, 74, 15.4),
     (18.0, 5.8, 76, 10.6), (9.9, 0.1, 74, 6.4), (2.9, -4.9, 66, 5.0)),
))


def monthly_normal(region: Region, month: int) -> tuple[float, float, float, float]:
    return region.normals[month - 1]


def daily_normal(region: Region, d: date) -> tuple[float, float, float, float]:
    """Linearly interpolate monthly normals to a smooth daily normal
    (tmax, tmin, precip_mm_per_day, srad)."""
    # Anchor normals at mid-month, interpolate between adjacent months.
    m = d.month
    mid = 15
    if d.day >= mid:
        m0, m1 = m, m % 12 + 1
        frac = (d.day - mid) / 30.0
    else:
        m0, m1 = (m - 2) % 12 + 1, m
        frac = (d.day + 15) / 30.0
    a = region.normals[m0 - 1]
    b = region.normals[m1 - 1]
    tmax = a[0] + (b[0] - a[0]) * frac
    tmin = a[1] + (b[1] - a[1]) * frac
    pr = (a[2] + (b[2] - a[2]) * frac) / 30.0
    sr = a[3] + (b[3] - a[3]) * frac
    return (tmax, tmin, pr, sr)
