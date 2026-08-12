"""OleoCast — soybean yield & oil composition prediction from public weather data.

Core package layout:
    soyoil.phenology   — GDD + photoperiod growth-stage model (VE..R8)
    soyoil.geography   — reference geography (counties, maturity groups, climate normals)
    soyoil.weather     — daily weather dataclasses, connectors, offline generator
    soyoil.features    — stage-window feature engineering
    soyoil.agronomy    — published weather -> yield/oil/fatty-acid response functions
    soyoil.simulate    — synthetic training-data generator built on soyoil.agronomy
    soyoil.train       — model training, leave-one-year-out validation, conformal intervals
    soyoil.predict     — in-season prediction service used by the API
    soyoil.processing  — crush economics / oil-extraction value model
"""

__version__ = "0.1.0"
