"""Crush economics: translate seed composition into processing value.

Standard soybean crush arithmetic per 60-lb bushel:
  * Oil recovered = 60 lb × oil% × extraction efficiency. Oil% here is the
    industry as-is (13%-moisture) basis, so no additional moisture deduction:
    19% oil × 60 lb × 0.96 recovery ≈ 10.9 lb — the commonly quoted
    "~11 lb oil per bushel".
  * Meal produced ≈ 44 lb of ~47.5%-protein meal per bushel; meal protein is
    tied to seed protein by mass balance (seed protein lb ÷ meal lb), not an
    arbitrary scale factor.
  * Estimated Processing Value (EPV) = oil lb × oil price + meal lb × meal
    price; crush margin = EPV − bean cost.

Oil-quality adjustments follow processing practice: high linolenic oil is
less oxidatively stable (discount for frying/food uses), high oleic earns a
premium. These are configurable — defaults are illustrative, not quotes.
"""

from __future__ import annotations

from dataclasses import dataclass

BU_LB = 60.0
SOLVENT_RECOVERY = 0.96           # fraction of seed oil recovered by solvent crush
MEAL_YIELD_LB_PER_BU = 44.0       # dehulled meal at standard composition
PROTEIN_TO_MEAL_LOSS = 0.97       # protein retained through crush into meal


@dataclass
class CrushAssumptions:
    oil_price_usd_lb: float = 0.47
    meal_price_usd_ton: float = 340.0
    bean_price_usd_bu: float = 10.60
    hi_oleic_premium_usd_lb: float = 0.055   # applies above 40% oleic
    hi_linolenic_discount_usd_lb: float = 0.02  # applies above 9% linolenic


@dataclass
class CrushResult:
    oil_lb_per_bu: float
    meal_lb_per_bu: float
    meal_protein_pct: float
    oil_value_usd_bu: float
    meal_value_usd_bu: float
    epv_usd_bu: float
    gross_margin_usd_bu: float
    oil_quality_adj_usd_lb: float
    oil_lb_per_ac: float
    epv_usd_ac: float


def crush_value(
    oil_pct: float,
    protein_pct: float,
    yield_bu_ac: float,
    oleic_pct: float = 23.0,
    linolenic_pct: float = 8.0,
    a: CrushAssumptions | None = None,
) -> CrushResult:
    a = a or CrushAssumptions()

    # Oil % is the as-is (13% moisture) basis the industry trades on — apply
    # it to the full 60-lb bushel, then extraction efficiency.
    oil_lb = BU_LB * (oil_pct / 100.0) * SOLVENT_RECOVERY
    # Meal mass shifts slightly opposite to oil content.
    meal_lb = MEAL_YIELD_LB_PER_BU + 0.4 * (21.0 - oil_pct)
    # Mass balance: seed protein lb flows into the meal (minus small loss).
    # 35% seed protein × 60 lb ÷ 44 lb meal ≈ 46-48% meal protein.
    meal_protein = (BU_LB * protein_pct / 100.0) * PROTEIN_TO_MEAL_LOSS / meal_lb * 100.0

    adj = 0.0
    if oleic_pct > 40.0:
        adj += a.hi_oleic_premium_usd_lb
    if linolenic_pct > 9.0:
        adj -= a.hi_linolenic_discount_usd_lb

    oil_value = oil_lb * (a.oil_price_usd_lb + adj)
    # Meal price adjusted for protein vs 48% standard (pro-rata)
    meal_value = (meal_lb / 2000.0) * a.meal_price_usd_ton * (meal_protein / 48.0)
    epv = oil_value + meal_value

    return CrushResult(
        oil_lb_per_bu=round(oil_lb, 2),
        meal_lb_per_bu=round(meal_lb, 2),
        meal_protein_pct=round(meal_protein, 1),
        oil_value_usd_bu=round(oil_value, 2),
        meal_value_usd_bu=round(meal_value, 2),
        epv_usd_bu=round(epv, 2),
        gross_margin_usd_bu=round(epv - a.bean_price_usd_bu, 2),
        oil_quality_adj_usd_lb=round(adj, 3),
        oil_lb_per_ac=round(oil_lb * yield_bu_ac, 1),
        epv_usd_ac=round(epv * yield_bu_ac, 2),
    )
