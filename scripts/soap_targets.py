"""Soapstock plant-targeting analysis for Feed Energy.

Ranks US/CA soybean crush plants as acidulation-feedstock targets by
combining, for each plant:
  * available wet soap after meal application (from the FE ledger)
  * isolation: great-circle distance to the nearest ACTIVE competing soap
    outlet (acidulators / integrated sites that would otherwise take the soap)
  * freight distance to Feed Energy, Des Moines
  * relationship status (existing FE supplier vs. new; over-served; integrated)

Outputs:
  data/soap_targets_ranked.csv   — ranked list for the deal team
  data/soap_targets.json         — payload for the interactive artifact

Run: .venv/bin/python scripts/soap_targets.py
"""

from __future__ import annotations

import csv
import json
import math
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Acidulation outlets (soap demand points). Locations for RUNION and PULLMAN
# are unknown shorthand in the source workbook — excluded from the default
# isolation calc until confirmed; the artifact lets you toggle outlets.
# ---------------------------------------------------------------------------
OUTLETS = [
    {"id": "fe",       "name": "Feed Energy — Des Moines, IA", "lat": 41.59, "lon": -93.62, "kind": "fe",         "default_on": False},
    {"id": "memphis",  "name": "Valley — Memphis, TN",         "lat": 35.15, "lon": -90.05, "kind": "competitor", "default_on": True},
    {"id": "stpaul",   "name": "St. Paul, MN",                 "lat": 44.95, "lon": -93.09, "kind": "competitor", "default_on": True},
    {"id": "goldcoast","name": "Gold Coast — Brandon, MS",     "lat": 32.27, "lon": -90.00, "kind": "competitor", "default_on": True},
    # In-house acidulation (soap never leaves site — modeled as demand sinks
    # for neighboring supply only if toggled on):
    {"id": "decatur_il","name": "ADM Decatur, IL (integrated)","lat": 39.84, "lon": -88.95, "kind": "integrated", "default_on": True},
    {"id": "windsor",  "name": "ADM Windsor, ON (integrated)", "lat": 42.30, "lon": -83.02, "kind": "integrated", "default_on": True},
    {"id": "mankato",  "name": "CHS Mankato, MN (integrated)", "lat": 44.16, "lon": -94.00, "kind": "integrated", "default_on": True},
]

# ---------------------------------------------------------------------------
# Plant ledger — parsed from the FE workbook (values as provided).
# (city, state, operator, capacity bu/yr, soap %, wet soap lbs, soap-on-meal %,
#  remaining soap lbs, FE normal lbs, FE historical lbs, avail-after-meal lbs,
#  lat, lon, note)
# ---------------------------------------------------------------------------
P = [
 ("Decatur","AL","Bunge",35_000_000,1.75,21_293_685,30,14_905_580,262_000,0,14_643_580,34.61,-86.98,""),
 ("Guntersville","AL","Cargill",30_000_000,1.75,18_251_730,30,12_776_211,0,0,0,34.36,-86.29,""),
 ("Stuttgart","AR","Riceland Foods",30_000_000,1.75,18_251_730,30,12_776_211,0,0,0,34.50,-91.55,""),
 ("Gainesville","GA","Cargill",25_000_000,1.75,15_209_775,30,10_646_843,0,6_453_000,4_193_843,34.30,-83.83,""),
 ("Valdosta","GA","ADM",30_000_000,1.75,18_251_730,30,12_776_211,0,0,0,30.85,-83.28,""),
 ("Bloomington","IL","Cargill",35_000_000,1.75,21_293_685,30,14_905_580,0,0,0,40.48,-88.99,""),
 ("Cairo","IL","Bunge-Chevron JV",52_000_000,1.75,31_636_332,30,22_145_432,0,0,0,37.01,-89.18,""),
 ("Decatur (Plant 1)","IL","ADM",70_000_000,4.00,97_342_560,30,68_139_792,2_605_000,12_108_000,56_031_792,39.84,-88.95,"integrated on-site acidulation"),
 ("Decatur (Plant 2)","IL","ADM",60_000_000,1.75,36_503_460,30,25_552_422,0,0,0,39.84,-88.95,"integrated on-site acidulation"),
 ("Gibson City","IL","Bunge",25_000_000,1.75,15_209_775,30,10_646_843,0,0,0,40.46,-88.37,""),
 ("Gilman","IL","Incobrasa Industries",40_000_000,1.75,24_335_640,30,17_034_948,182_000,0,16_852_948,40.77,-87.99,""),
 ("Quincy (Plant 1)","IL","ADM",55_000_000,1.75,33_461_505,0,33_461_505,1_830_000,238_000,31_631_505,39.94,-91.41,""),
 ("Quincy (Plant 2)","IL","ADM",45_000_000,1.75,27_377_595,0,27_377_595,0,0,0,39.94,-91.41,""),
 ("Claypool","IN","Louis Dreyfus Company",30_000_000,1.75,18_251_730,30,12_776_211,0,0,0,41.13,-85.88,""),
 ("Decatur","IN","Bunge",50_000_000,1.75,30_419_550,30,21_293_685,0,0,0,40.83,-84.93,""),
 ("Frankfort","IN","ADM",40_000_000,1.75,24_335_640,30,17_034_948,45_000,0,16_989_948,40.28,-86.51,""),
 ("Lafayette","IN","Cargill",40_000_000,1.75,24_335_640,30,17_034_948,0,0,0,40.42,-86.87,""),
 ("Morristown","IN","Bunge",25_000_000,1.75,15_209_775,100,0,0,0,0,39.67,-85.70,"100% soap to meal"),
 ("Mt. Vernon","IN","CGB Enterprises",25_000_000,1.75,15_209_775,30,10_646_843,0,0,0,37.93,-87.90,""),
 ("Seymour","IN","White River Nutrition",15_000_000,1.75,9_125_865,30,6_388_106,0,0,0,38.96,-85.89,""),
 ("Alta","IA","Platinum Crush LLC",40_000_000,2.25,31_288_680,100,0,0,65_000,-65_000,42.67,-95.29,"100% soap to meal"),
 ("Cedar Rapids","IA","Cargill",60_000_000,1.75,36_503_460,100,0,0,0,0,41.98,-91.66,"100% soap to meal"),
 ("Council Bluffs","IA","Bunge",80_000_000,4.00,121_478_400,10,109_330_560,112_443_000,78_639_000,-3_112_440,41.26,-95.86,"FE anchor volume"),
 ("Creston","IA","White River Nutrition",15_000_000,1.75,9_125_865,30,6_388_106,0,3_364_000,3_024_106,41.06,-94.36,""),
 ("Des Moines","IA","ADM",50_000_000,1.75,30_419_550,30,21_293_685,22_017_000,25_082_000,-3_788_315,41.59,-93.62,"FE anchor volume"),
 ("Eagle Grove","IA","AGP",55_000_000,1.75,33_461_505,30,23_423_054,30_000,0,23_393_054,42.66,-93.90,""),
 ("Emmetsburg","IA","AGP",35_000_000,1.75,21_293_685,30,14_905_580,0,0,0,43.11,-94.68,""),
 ("Iowa Falls","IA","Cargill",30_000_000,4.00,41_718_240,30,29_202_768,12_315_000,27_482_000,1_720_768,42.52,-93.25,""),
 ("Manning","IA","AGP",30_000_000,1.75,18_251_730,30,12_776_211,0,0,0,41.91,-95.06,""),
 ("Mason City","IA","AGP",30_000_000,1.75,18_251_730,30,12_776_211,0,0,0,43.15,-93.20,""),
 ("Sergeant Bluff","IA","AGP",45_000_000,1.75,21_293_685,0,21_293_685,18_078_000,11_862_000,3_215_685,42.40,-96.35,"70% utilization"),
 ("Sheldon","IA","AGP",35_000_000,1.75,21_293_685,30,14_905_580,0,0,0,43.18,-95.85,""),
 ("Shell Rock","IA","Shell Rock Soy Processing",39_000_000,2.25,30_506_463,100,0,0,0,0,42.71,-92.58,"100% soap to meal"),
 ("Sioux City","IA","Cargill",40_000_000,4.00,55_624_320,30,38_937_024,27_786_000,29_780_000,9_157_024,42.50,-96.40,""),
 ("Cherryvale","KS","Bartlett (Savage)",25_000_000,4.00,34_765_200,30,24_335_640,0,27_412_000,-3_076_360,37.27,-95.55,""),
 ("Emporia","KS","Bunge",30_000_000,1.75,18_251_730,30,12_776_211,0,0,0,38.40,-96.18,""),
 ("Goodland","KS","Scoular",25_000_000,1.75,15_209_775,30,10_646_843,0,0,0,39.35,-101.71,""),
 ("Wichita","KS","Cargill",35_000_000,1.75,21_293_685,30,14_905_580,0,1_082_000,13_823_580,37.69,-97.34,""),
 ("Owensboro","KY","Cargill-Owensboro Grain JV",30_000_000,1.75,18_251_730,30,12_776_211,0,3_834_000,8_942_211,37.77,-87.11,""),
 ("Destrehan","LA","Bunge-Chevron JV",52_000_000,1.75,31_636_332,30,22_145_432,0,0,0,29.94,-90.35,""),
 ("Salisbury","MD","Perdue AgriBusiness",25_000_000,1.75,15_209_775,30,10_646_843,0,0,0,38.36,-75.60,""),
 ("Ithaca","MI","Zeeland Farm Services",15_000_000,1.75,9_125_865,30,6_388_106,0,0,0,43.29,-84.61,""),
 ("Zeeland","MI","Zeeland Farm Services",25_000_000,1.75,15_209_775,30,10_646_843,0,0,0,42.81,-86.02,""),
 ("Brewster","MN","Minnesota Soybean Processors",40_000_000,4.00,55_624_320,30,38_937_024,34_910_000,24_628_000,4_027_024,43.70,-95.47,""),
 ("Dawson","MN","AGP",40_000_000,0.00,0,30,0,0,0,0,44.93,-96.05,"no soap production"),
 ("Fairmont","MN","CHS",72_000_000,1.75,43_804_152,30,30_662_906,0,0,0,43.65,-94.46,""),
 ("Mankato (ADM)","MN","ADM",50_000_000,4.00,69_530_400,30,48_671_280,44_733_000,5_879_000,3_938_280,44.16,-94.00,""),
 ("Mankato (CHS)","MN","CHS",45_000_000,1.75,27_377_595,30,19_164_317,2_494_000,12_066_000,7_098_317,44.16,-94.00,"integrated acidulation"),
 ("Deerfield","MO","ADM",25_000_000,1.75,15_209_775,30,10_646_843,10_680_000,25_041_000,-14_394_158,37.83,-94.50,"over-served"),
 ("Kansas City","MO","Cargill",55_000_000,1.75,33_461_505,30,23_423_054,0,0,0,39.10,-94.58,""),
 ("Mexico","MO","ADM",50_000_000,1.75,30_419_550,30,21_293_685,0,0,0,39.17,-91.88,""),
 ("St. Joseph","MO","AGP",30_000_000,1.75,18_251_730,0,18_251_730,10_493_000,12_205_000,6_046_730,39.77,-94.85,""),
 ("David City","NE","AGP",35_000_000,0.00,0,30,0,0,0,0,41.25,-97.13,"no soap production"),
 ("Fremont","NE","ADM",40_000_000,1.75,24_335_640,30,17_034_948,0,0,0,41.44,-96.50,""),
 ("Hastings","NE","AGP",35_000_000,1.75,21_293_685,30,14_905_580,4_970_000,3_269_000,9_935_580,40.59,-98.39,""),
 ("Lincoln","NE","ADM",45_000_000,1.75,27_377_595,30,19_164_317,8_994_000,7_456_000,10_170_317,40.81,-96.68,""),
 ("Norfolk","NE","Norfolk Crush",35_000_000,2.25,27_377_595,100,0,0,0,0,42.03,-97.42,"100% soap to meal"),
 ("Fayetteville","NC","Cargill",30_000_000,1.75,18_251_730,30,12_776_211,0,2_964_000,9_812_211,35.05,-78.88,""),
 ("Cofield","NC","Perdue AgriBusiness",15_000_000,1.75,9_125_865,30,6_388_106,0,0,0,36.36,-76.91,""),
 ("Casselton","ND","North Dakota Soybean Processors",42_500_000,1.75,25_856_618,90,2_585_662,0,6_897_000,-4_311_338,46.90,-97.21,"90% soap to meal; over-served"),
 ("Spiritwood","ND","ADM-Marathon JV",50_000_000,1.75,30_419_550,60,12_167_820,89_000,2_154_000,10_013_820,46.94,-98.56,""),
 ("Bellevue","OH","Bunge",30_000_000,1.75,18_251_730,30,12_776_211,0,0,0,41.27,-82.84,""),
 ("Delphos","OH","Bunge",25_000_000,1.75,15_209_775,30,10_646_843,0,0,0,40.84,-84.34,""),
 ("Fostoria","OH","ADM",30_000_000,1.75,18_251_730,30,12_776_211,0,0,0,41.16,-83.42,""),
 ("Sidney","OH","Cargill",60_000_000,1.75,36_503_460,30,25_552_422,3_711_000,2_821_000,21_841_422,40.28,-84.16,""),
 ("Bainbridge","PA","Perdue AgriBusiness",17_500_000,1.75,10_646_843,30,7_452_790,0,0,0,40.09,-76.67,""),
 ("Aberdeen","SD","AGP",45_000_000,1.75,27_377_595,30,19_164_317,0,0,0,45.46,-98.49,""),
 ("Volga","SD","South Dakota Soybean Processors",35_000_000,1.75,21_293_685,30,14_905_580,6_201_000,1_075_000,8_704_580,44.32,-96.93,""),
 ("Chesapeake","VA","Perdue AgriBusiness",30_000_000,1.75,18_251_730,30,12_776_211,0,0,0,36.72,-76.28,""),
 ("South Charleston","OH","Louis Dreyfus Company",60_000_000,1.75,36_503_460,30,25_552_422,0,0,0,39.82,-83.63,"announced — not yet operating"),
 ("Pemiscot County","MO","Cargill",62_000_000,1.75,37_720_242,30,26_404_169,0,0,0,36.20,-89.80,"on hold"),
 ("Windsor","ON","ADM",46_000_000,1.75,15_547_770,0,15_547_770,0,0,0,42.30,-83.02,"50% utilization; integrated acidulation"),
]


def haversine_mi(lat1, lon1, lat2, lon2):
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def classify(plant, iso_mi):
    """Status + target tier. Tiering is deliberately simple and transparent:
    volume is what a deal is worth; isolation is how winnable it is."""
    city, st, op, cap, soap_pct, wet, on_meal, remaining, fe_norm, fe_hist, avail, lat, lon, note = plant
    if cap == 0 or soap_pct == 0:
        return "no-soap", None
    if "announced" in note or "on hold" in note:
        return "future", None
    if on_meal >= 90:
        return "integrated-meal", None
    if "integrated acidulation" in note or "on-site acidulation" in note:
        return "integrated-acid", None
    if avail < 0:
        return "over-served", None
    existing = fe_norm > 0 or fe_hist > 0
    vol_m = (avail if avail > 0 else remaining) / 1e6
    if vol_m < 3:
        return ("existing" if existing else "small"), None
    # tiers on volume x isolation
    if vol_m >= 10 and iso_mi >= 250:
        tier = "A"
    elif vol_m >= 8 or (vol_m >= 4 and iso_mi >= 300):
        tier = "B"
    else:
        tier = "C"
    return ("existing" if existing else "new"), tier


def main():
    active_outlets = [o for o in OUTLETS if o["default_on"]]
    fe = next(o for o in OUTLETS if o["id"] == "fe")
    rows = []
    for plant in P:
        city, st, op, cap, soap_pct, wet, on_meal, remaining, fe_norm, fe_hist, avail, lat, lon, note = plant
        dists = {o["id"]: round(haversine_mi(lat, lon, o["lat"], o["lon"]), 0) for o in OUTLETS}
        iso = min(dists[o["id"]] for o in active_outlets)
        status, tier = classify(plant, iso)
        vol_m = (avail if avail > 0 else remaining) / 1e6
        score = round(vol_m * min(iso, 600) / 100.0, 1) if tier else 0.0
        rows.append({
            "city": city, "state": st, "operator": op,
            "capacity_mbu": cap / 1e6, "soap_pct": soap_pct,
            "wet_soap_mlbs": round(wet / 1e6, 2),
            "soap_on_meal_pct": on_meal,
            "avail_after_meal_mlbs": round(avail / 1e6, 2),
            "remaining_soap_mlbs": round(remaining / 1e6, 2),
            "fe_current_mlbs": round((fe_norm + fe_hist) / 1e6, 2),
            "dist_to_fe_mi": dists["fe"],
            "isolation_mi": iso,
            "status": status, "tier": tier or "",
            "score": score,
            "lat": lat, "lon": lon,
            "note": note,
            "outlet_dists": dists,
        })

    rows.sort(key=lambda r: (-(r["tier"] != ""), r["tier"] or "Z", -r["score"]))

    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    csv_path = os.path.join(HERE, "data", "soap_targets_ranked.csv")
    cols = ["tier", "score", "city", "state", "operator", "status",
            "avail_after_meal_mlbs", "remaining_soap_mlbs", "wet_soap_mlbs",
            "soap_on_meal_pct", "isolation_mi", "dist_to_fe_mi",
            "fe_current_mlbs", "capacity_mbu", "soap_pct", "note"]
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    json_path = os.path.join(HERE, "data", "soap_targets.json")
    json.dump({"outlets": OUTLETS, "plants": rows}, open(json_path, "w"), indent=1)

    targets = [r for r in rows if r["tier"]]
    print(f"{len(rows)} plants analyzed, {len(targets)} scored targets")
    print(f"\n{'Tier':4s} {'Score':>6s} {'Plant':34s} {'Avail Mlbs':>10s} {'Isol mi':>8s} {'FE mi':>6s} {'Status':9s}")
    for r in targets[:20]:
        name = f"{r['operator'][:18]} {r['city']}, {r['state']}"
        print(f"{r['tier']:4s} {r['score']:6.1f} {name:34s} "
              f"{max(r['avail_after_meal_mlbs'], r['remaining_soap_mlbs']):10.1f} "
              f"{r['isolation_mi']:8.0f} {r['dist_to_fe_mi']:6.0f} {r['status']:9s}")


if __name__ == "__main__":
    main()
