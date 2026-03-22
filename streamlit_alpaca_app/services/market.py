from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .alpaca_api import AlpacaAPI


DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "NFLX", "AMD", "INTC",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "PYPL", "SHOP", "UBER",
    "XOM", "CVX", "SLB", "COP", "PFE", "JNJ", "LLY", "UNH", "MRK", "ABBV",
]


def _unique_symbols(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = str(value).upper().strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


BUSINESS_FOCUS_UNIVERSES: dict[str, list[str]] = {
    "All Market": DEFAULT_UNIVERSE,
    "Alternative Asset Managers": [
        "BX", "KKR", "OWL", "APO", "ARES", "BAM", "CG", "TPG",
    ],
    "Housing": [
        "HD", "LOW", "DHI", "LEN", "PHM", "TOL", "NVR", "KBH", "BLD", "SHW", "WHR", "Z", "RDFN",
    ],
    "Retail": [
        "AMZN", "WMT", "COST", "TGT", "TJX", "ROST", "BURL", "DG", "DLTR", "BBY", "FIVE", "ETSY", "SHOP",
    ],
    "Media": [
        "DIS", "CMCSA", "CHTR", "WBD", "PARA", "FOXA", "NYT", "ROKU",
    ],
    "Social Media & Entertainment": [
        "META", "NFLX", "SNAP", "PINS", "SPOT", "RBLX", "EA", "TTWO", "DIS",
    ],
    "Advertising": [
        "GOOGL", "META", "TTD", "APP", "ROKU", "SNAP", "PINS", "OMC", "IPG", "MGNI", "CRTO",
    ],
    "Commodity": [
        "XOM", "CVX", "COP", "SLB", "FCX", "NEM", "AA", "MOS", "CF", "MP",
    ],
    "Payments & Commerce": [
        "V", "MA", "PYPL", "SHOP", "AMZN", "SQ", "COIN", "AFRM",
    ],
    "Travel & Mobility": [
        "UBER", "ABNB", "BKNG", "EXPE", "DAL", "UAL", "MAR", "HLT",
    ],
    "Healthcare & Life Sciences": [
        "PFE", "JNJ", "LLY", "UNH", "MRK", "ABBV", "ISRG", "TMO",
    ],
}

BUSINESS_FOCUS_DESCRIPTIONS: dict[str, str] = {
    "All Market": "Broad liquid universe across major consumer, technology, finance, energy, and healthcare names.",
    "Alternative Asset Managers": "Private capital, credit, and alternative-asset managers that often move together through fundraising, fee-related earnings, deployment pace, and financing conditions.",
    "Housing": "Homebuilding, renovation, housing transactions, and home-linked product businesses.",
    "Retail": "Businesses that primarily sell goods to end consumers through stores or digital storefronts.",
    "Media": "Content distribution, cable, studios, streaming platforms, and broad media networks.",
    "Social Media & Entertainment": "Audience attention businesses driven by social graphs, streaming, music, and interactive entertainment.",
    "Advertising": "Businesses monetizing demand generation, ad spend, ad software, or audience targeting.",
    "Commodity": "Commodity-linked businesses spanning energy, mining, metals, fertilizer, and strategic-material supply.",
    "Payments & Commerce": "Transaction rails, merchant tooling, checkout, and adjacent commerce enablement.",
    "Travel & Mobility": "Ride-sharing, travel booking, airlines, hotels, and travel demand platforms.",
    "Healthcare & Life Sciences": "Drug makers, managed care, medical tools, and life-science suppliers.",
}

COMMODITY_PROXY_METADATA: dict[str, dict[str, str]] = {
    "DBC": {
        "name": "Invesco DB Commodity Index Tracking Fund",
        "commodity": "Broad commodity basket",
        "description": "Diversified commodity futures proxy spanning energy, metals, and agriculture.",
    },
    "PDBC": {
        "name": "Invesco Optimum Yield Diversified Commodity Strategy",
        "commodity": "Broad commodity basket",
        "description": "Broad commodity exposure with a futures-roll approach designed to reduce contango drag.",
    },
    "USO": {
        "name": "United States Oil Fund",
        "commodity": "WTI crude oil",
        "description": "Near-dated WTI crude oil proxy used to track the direction of the US oil market.",
    },
    "BNO": {
        "name": "United States Brent Oil Fund",
        "commodity": "Brent crude oil",
        "description": "Brent crude proxy that helps separate global oil pricing from domestic WTI dynamics.",
    },
    "UNG": {
        "name": "United States Natural Gas Fund",
        "commodity": "Natural gas",
        "description": "Natural gas futures proxy tied to storage, weather, and power-demand swings.",
    },
    "UGA": {
        "name": "United States Gasoline Fund",
        "commodity": "Gasoline",
        "description": "Refined fuel proxy that can react differently from crude when crack spreads widen or narrow.",
    },
    "GLD": {
        "name": "SPDR Gold Shares",
        "commodity": "Gold",
        "description": "Gold proxy used to track defensive hard-asset demand and real-rate sensitivity.",
    },
    "SLV": {
        "name": "iShares Silver Trust",
        "commodity": "Silver",
        "description": "Silver proxy with both precious-metal and industrial-demand characteristics.",
    },
    "PPLT": {
        "name": "Aberdeen Physical Platinum Shares ETF",
        "commodity": "Platinum",
        "description": "Platinum proxy tied to precious-metal flows and industrial/autocatalyst demand.",
    },
    "PALL": {
        "name": "abrdn Physical Palladium Shares ETF",
        "commodity": "Palladium",
        "description": "Palladium proxy tied to autocatalyst demand, industrial tightening, and precious-metal spillover.",
    },
    "CPER": {
        "name": "United States Copper Index Fund",
        "commodity": "Copper",
        "description": "Copper futures proxy often used as a market read on construction and industrial momentum.",
    },
    "DBB": {
        "name": "Invesco DB Base Metals Fund",
        "commodity": "Base metals",
        "description": "Basket proxy for industrial metals such as aluminum, zinc, and copper-linked demand.",
    },
    "REMX": {
        "name": "VanEck Rare Earth and Strategic Metals ETF",
        "commodity": "Rare earths",
        "description": "Strategic-materials proxy for rare earth supply chains and magnet-intensive demand.",
    },
    "LIT": {
        "name": "Global X Lithium & Battery Tech ETF",
        "commodity": "Lithium and battery materials",
        "description": "Lithium and battery-materials proxy tied to EV and energy-storage demand.",
    },
    "URA": {
        "name": "Global X Uranium ETF",
        "commodity": "Uranium",
        "description": "Uranium and nuclear-fuel-cycle proxy tied to long-duration power demand and energy-security themes.",
    },
    "DBA": {
        "name": "Invesco DB Agriculture Fund",
        "commodity": "Agriculture basket",
        "description": "Broad agriculture proxy covering key crop markets and food-input pricing pressure.",
    },
    "CORN": {
        "name": "Teucrium Corn Fund",
        "commodity": "Corn",
        "description": "Corn proxy tied to feed, ethanol, and row-crop supply conditions.",
    },
    "WEAT": {
        "name": "Teucrium Wheat Fund",
        "commodity": "Wheat",
        "description": "Wheat proxy sensitive to global crop conditions, export flows, and food inflation.",
    },
    "SOYB": {
        "name": "Teucrium Soybean Fund",
        "commodity": "Soybeans",
        "description": "Soybean proxy tied to global protein demand, crush margins, and weather risk.",
    },
    "JO": {
        "name": "iPath Series B Bloomberg Coffee Subindex",
        "commodity": "Coffee",
        "description": "Coffee proxy that helps track soft-commodity price spikes and weather-driven shortages.",
    },
    "CANE": {
        "name": "Teucrium Sugar Fund",
        "commodity": "Sugar",
        "description": "Sugar proxy linked to soft-commodity supply, Brazilian crop conditions, and energy crossover.",
    },
    "NIB": {
        "name": "iPath Series B Bloomberg Cocoa Subindex",
        "commodity": "Cocoa",
        "description": "Cocoa proxy sensitive to West African supply stress and food-inflation pressure in softs.",
    },
    "BAL": {
        "name": "iPath Series B Bloomberg Cotton Subindex",
        "commodity": "Cotton",
        "description": "Cotton proxy linked to apparel supply chains, weather, and textile demand.",
    },
    "BDRY": {
        "name": "Breakwave Dry Bulk Shipping ETF",
        "commodity": "Dry bulk shipping",
        "description": "Freight-rate proxy that helps capture shipping pressure across grains, coal, ores, and industrial materials.",
    },
}

COMMODITY_FOCUS_UNIVERSES: dict[str, list[str]] = {
    "Broad Commodity Market": [
        "DBC", "PDBC", "USO", "BNO", "UNG", "UGA",
        "GLD", "SLV", "PPLT", "PALL",
        "CPER", "DBB", "REMX", "LIT", "URA",
        "DBA", "CORN", "WEAT", "SOYB", "JO", "CANE", "NIB", "BAL",
        "BDRY",
    ],
    "Energy & Oil": ["USO", "BNO", "UNG", "UGA"],
    "Precious Metals": ["GLD", "SLV", "PPLT", "PALL"],
    "Industrial Metals": ["CPER", "DBB"],
    "Rare Earths & Strategic Materials": ["REMX", "LIT", "URA"],
    "Softs & Agriculture": ["DBA", "CORN", "WEAT", "SOYB", "JO", "CANE", "NIB", "BAL"],
    "Shipping & Logistics": ["BDRY"],
}

COMMODITY_FOCUS_DESCRIPTIONS: dict[str, str] = {
    "Broad Commodity Market": "Cross-market commodity lens spanning oil, refined fuels, metals, agriculture, strategic materials, and freight pressure.",
    "Energy & Oil": "Oil, gas, and refined-fuel proxies that respond fastest to energy shocks, geopolitics, and refinery/storage conditions.",
    "Precious Metals": "Gold, silver, platinum, and palladium proxies useful for reading defensive flows and industrial precious demand.",
    "Industrial Metals": "Copper and base-metal proxies tied closely to construction, manufacturing, and China-sensitive demand.",
    "Rare Earths & Strategic Materials": "Strategic-materials proxies linked to battery supply chains, magnets, uranium, and electrification demand.",
    "Softs & Agriculture": "Crop and soft-commodity proxies such as coffee, cocoa, cotton, grains, and sugar, useful for food-inflation and weather shocks.",
    "Shipping & Logistics": "Freight-rate proxies that can signal transmission pressure moving through raw-material supply chains.",
}

COMMODITY_REFERENCE_SYMBOLS = _unique_symbols(
    ["PDBC", "USO", "BNO", "UNG", "GLD", "SLV", "CPER", "DBB", "REMX", "DBA", "CORN", "JO", "BDRY"]
)

COMMODITY_DEPENDENCY_EDGES: list[dict[str, object]] = [
    {
        "source": "USO",
        "target": "UNG",
        "relation": "energy complex",
        "weight": 2.4,
        "description": "Oil and gas shocks often move together through macro energy demand and supply stress.",
    },
    {
        "source": "USO",
        "target": "UGA",
        "relation": "refining spread transmission",
        "weight": 2.2,
        "description": "Crude moves can transmit into gasoline with changing refinery margins and product inventories.",
    },
    {
        "source": "USO",
        "target": "CANE",
        "relation": "biofuel crossover",
        "weight": 1.6,
        "description": "Higher oil can increase the relative value of sugarcane-linked ethanol routes.",
    },
    {
        "source": "USO",
        "target": "CORN",
        "relation": "biofuel crossover",
        "weight": 1.5,
        "description": "Corn and oil interact through ethanol economics and transport input costs.",
    },
    {
        "source": "UNG",
        "target": "DBA",
        "relation": "fertilizer cost transmission",
        "weight": 2.7,
        "description": "Natural gas drives nitrogen fertilizer costs, which then feed into crop markets.",
    },
    {
        "source": "UNG",
        "target": "WEAT",
        "relation": "fertilizer cost transmission",
        "weight": 2.2,
        "description": "Wheat costs can react indirectly to fertilizer and energy input pressure.",
    },
    {
        "source": "UNG",
        "target": "SOYB",
        "relation": "fertilizer cost transmission",
        "weight": 2.0,
        "description": "Soybeans also absorb fertilizer and fuel cost pressure.",
    },
    {
        "source": "USO",
        "target": "BDRY",
        "relation": "freight fuel pressure",
        "weight": 1.8,
        "description": "Oil price shocks can feed into freight costs and dry-bulk shipping economics.",
    },
    {
        "source": "CPER",
        "target": "DBB",
        "relation": "industrial metals complex",
        "weight": 2.6,
        "description": "Copper often leads the broader industrial-metals complex.",
    },
    {
        "source": "CPER",
        "target": "REMX",
        "relation": "electrification buildout",
        "weight": 2.1,
        "description": "Copper and rare earths both rise when grid and electrification demand strengthens.",
    },
    {
        "source": "CPER",
        "target": "LIT",
        "relation": "battery supply chain",
        "weight": 2.0,
        "description": "Copper and lithium both respond to EV and storage buildout cycles.",
    },
    {
        "source": "REMX",
        "target": "LIT",
        "relation": "strategic materials chain",
        "weight": 1.8,
        "description": "Rare earths and lithium sit in adjacent electrification and battery supply chains.",
    },
    {
        "source": "URA",
        "target": "LIT",
        "relation": "electrification power buildout",
        "weight": 1.4,
        "description": "Uranium and lithium can co-move when long-duration power and electrification spending both strengthen.",
    },
    {
        "source": "GLD",
        "target": "SLV",
        "relation": "precious metals transmission",
        "weight": 1.9,
        "description": "Gold leadership often spills over into silver as risk appetite expands within precious metals.",
    },
    {
        "source": "SLV",
        "target": "PPLT",
        "relation": "industrial precious spillover",
        "weight": 1.4,
        "description": "Silver and platinum can co-move when industrial precious demand improves.",
    },
    {
        "source": "PPLT",
        "target": "PALL",
        "relation": "autocatalyst metals chain",
        "weight": 1.5,
        "description": "Platinum and palladium can rotate together through autocatalyst substitution and industrial demand.",
    },
    {
        "source": "DBA",
        "target": "CORN",
        "relation": "crop basket leadership",
        "weight": 1.6,
        "description": "Corn can lead broader agricultural baskets during feed or weather stress.",
    },
    {
        "source": "DBA",
        "target": "WEAT",
        "relation": "crop basket leadership",
        "weight": 1.5,
        "description": "Wheat can transmit grain-market pressure through the broader crop basket.",
    },
    {
        "source": "DBA",
        "target": "SOYB",
        "relation": "crop basket leadership",
        "weight": 1.5,
        "description": "Soybeans can transmit protein and crush-margin demand through agriculture baskets.",
    },
    {
        "source": "DBA",
        "target": "JO",
        "relation": "food inflation spillover",
        "weight": 1.3,
        "description": "Coffee often participates in broader food and weather-driven softs inflation waves.",
    },
    {
        "source": "DBA",
        "target": "NIB",
        "relation": "softs inflation spillover",
        "weight": 1.4,
        "description": "Cocoa can participate in wider softs inflation and supply-shock waves.",
    },
    {
        "source": "DBA",
        "target": "BAL",
        "relation": "softs inflation spillover",
        "weight": 1.2,
        "description": "Cotton can respond alongside broader agriculture and softs supply shocks.",
    },
    {
        "source": "BDRY",
        "target": "DBA",
        "relation": "shipping bottleneck transmission",
        "weight": 1.6,
        "description": "Dry-bulk freight spikes can transmit into agriculture and bulk-commodity landed costs.",
    },
]

_all_market_symbols = _unique_symbols(
    DEFAULT_UNIVERSE + [symbol for name, symbols in BUSINESS_FOCUS_UNIVERSES.items() if name != "All Market" for symbol in symbols]
)
BUSINESS_FOCUS_UNIVERSES["All Market"] = _all_market_symbols


def business_focus_options() -> list[str]:
    return list(BUSINESS_FOCUS_UNIVERSES.keys())


def business_focus_description(name: str) -> str:
    return BUSINESS_FOCUS_DESCRIPTIONS.get(str(name), "")


def business_focus_for_symbol(symbol: str) -> str:
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return "All Market"
    for name, symbols in BUSINESS_FOCUS_UNIVERSES.items():
        if name == "All Market":
            continue
        if normalized in {str(value).upper().strip() for value in symbols if str(value).strip()}:
            return name
    return "All Market"


def business_focus_universe(name: str) -> list[str]:
    label = str(name or "All Market")
    if label not in BUSINESS_FOCUS_UNIVERSES:
        label = "All Market"
    return list(BUSINESS_FOCUS_UNIVERSES[label])


def commodity_focus_options() -> list[str]:
    return list(COMMODITY_FOCUS_UNIVERSES.keys())


def commodity_focus_description(name: str) -> str:
    return COMMODITY_FOCUS_DESCRIPTIONS.get(str(name), "")


def commodity_focus_universe(name: str) -> list[str]:
    label = str(name or "Broad Commodity Market")
    if label not in COMMODITY_FOCUS_UNIVERSES:
        label = "Broad Commodity Market"
    return list(COMMODITY_FOCUS_UNIVERSES[label])


def extend_symbol_universe(symbols: list[str] | None, extra_symbols: list[str] | None = None) -> list[str]:
    return _unique_symbols(list(symbols or []) + list(extra_symbols or []))


def commodity_reference_universe() -> list[str]:
    return list(COMMODITY_REFERENCE_SYMBOLS)


def commodity_proxy_profile(symbol: str) -> dict[str, str]:
    normalized = str(symbol or "").upper().strip()
    meta = COMMODITY_PROXY_METADATA.get(normalized, {})
    return {
        "symbol": normalized,
        "name": str(meta.get("name") or normalized),
        "commodity": str(meta.get("commodity") or "Commodity proxy"),
        "description": str(meta.get("description") or "Commodity market proxy."),
    }


def commodity_dependency_graph(symbols: list[str] | None = None) -> pd.DataFrame:
    allowed = {str(symbol).upper().strip() for symbol in (symbols or COMMODITY_PROXY_METADATA.keys()) if str(symbol).strip()}
    rows: list[dict[str, object]] = []
    for edge in COMMODITY_DEPENDENCY_EDGES:
        source = str(edge.get("source") or "").upper().strip()
        target = str(edge.get("target") or "").upper().strip()
        if source not in allowed or target not in allowed:
            continue
        source_profile = commodity_proxy_profile(source)
        target_profile = commodity_proxy_profile(target)
        rows.append(
            {
                "source": source,
                "target": target,
                "source_name": source_profile["name"],
                "target_name": target_profile["name"],
                "source_commodity": source_profile["commodity"],
                "target_commodity": target_profile["commodity"],
                "relation": str(edge.get("relation") or ""),
                "weight": float(edge.get("weight") or 1.0),
                "description": str(edge.get("description") or ""),
            }
        )
    return pd.DataFrame(rows)


def _log_slope(series: pd.Series, window: int) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(values) < window or (values <= 0).any():
        return np.nan
    y = np.log(values.to_numpy(dtype=float))
    x = np.arange(len(y), dtype=float)
    slope, _ = np.polyfit(x, y, 1)
    return float(slope)


def _trend_r2(series: pd.Series, window: int) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(values) < window or (values <= 0).any():
        return np.nan
    y = np.log(values.to_numpy(dtype=float))
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot == 0:
        return 1.0
    r2 = 1.0 - (ss_res / ss_tot)
    return float(np.clip(r2, 0.0, 1.0))


def _sparkline(series: pd.Series, window: int, points: int = 24) -> list[float]:
    values = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(values) < max(8, min(window, 12)):
        return []
    sample_count = min(points, len(values))
    positions = np.linspace(0, len(values) - 1, sample_count).round().astype(int)
    positions = np.unique(positions)
    sampled = values.iloc[positions].astype(float).reset_index(drop=True)
    base = float(sampled.iloc[0]) if not sampled.empty else 0.0
    if base == 0:
        return []
    return [round((float(value) / base) * 100.0, 2) for value in sampled]


def _window_return_pct(series: pd.Series, window: int) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna().tail(window)
    if len(values) < window:
        return np.nan
    start = float(values.iloc[0])
    end = float(values.iloc[-1])
    if start == 0:
        return np.nan
    return ((end / start) - 1.0) * 100.0


def _ratio_minus_one(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0:
        return np.nan
    return float(numerator / denominator - 1.0)


def _mean_finite(values: list[float]) -> float:
    finite = [float(value) for value in values if np.isfinite(value)]
    if not finite:
        return np.nan
    return float(np.mean(finite))


def _rolling_compound(returns: pd.Series, window: int) -> pd.Series:
    clean = pd.to_numeric(returns, errors="coerce")
    if window <= 1:
        return clean
    return (1.0 + clean).rolling(window).apply(np.prod, raw=True) - 1.0


def _zscore(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    if len(valid) < 2:
        return pd.Series(np.zeros(len(numeric)), index=numeric.index, dtype=float)
    std = float(valid.std(ddof=0))
    if std == 0:
        return pd.Series(np.zeros(len(numeric)), index=numeric.index, dtype=float)
    return (numeric - float(valid.mean())) / std


def _rolling_beta(asset_returns: pd.Series, benchmark_returns: pd.Series, window: int) -> pd.Series:
    cov = pd.to_numeric(asset_returns, errors="coerce").rolling(window).cov(pd.to_numeric(benchmark_returns, errors="coerce"))
    var = pd.to_numeric(benchmark_returns, errors="coerce").rolling(window).var()
    beta = cov / var.replace(0, np.nan)
    return beta.replace([np.inf, -np.inf], np.nan)


def _normalized_price_matrix(close_matrix: pd.DataFrame) -> pd.DataFrame:
    normalized = close_matrix.copy()
    for col in normalized.columns:
        series = pd.to_numeric(normalized[col], errors="coerce")
        valid = series.dropna()
        if valid.empty or float(valid.iloc[0]) == 0:
            normalized[col] = np.nan
            continue
        normalized[col] = series / float(valid.iloc[0])
    return normalized


def _build_equal_weight_basket(normalized_matrix: pd.DataFrame) -> pd.DataFrame:
    basket = pd.DataFrame(index=normalized_matrix.index)
    basket["commodity_norm"] = normalized_matrix.mean(axis=1, skipna=True)
    basket = basket.dropna(subset=["commodity_norm"])
    if basket.empty:
        return basket
    basket["commodity_return"] = basket["commodity_norm"].pct_change()
    basket["commodity_close"] = basket["commodity_norm"] * 100.0
    return basket.reset_index().rename(columns={"index": "timestamp"})


def _phase_regime(compounding_momentum_pct: float, momentum_roc_pct: float, correlation_roc: float) -> str:
    if compounding_momentum_pct >= 0 and correlation_roc < 0:
        return "Decoupling leader"
    if compounding_momentum_pct >= 0 and momentum_roc_pct >= 0 and correlation_roc >= 0:
        return "Beta-linked breakout"
    if compounding_momentum_pct < 0 and correlation_roc >= 0:
        return "Crowded unwind"
    if compounding_momentum_pct < 0 and correlation_roc < 0:
        return "Washout reset"
    return "Transition"


def _commodity_regime(transmission_gap_pct: float, beta_now: float, beta_roc: float) -> str:
    if transmission_gap_pct >= 0 and beta_now >= 0.3 and beta_roc >= 0:
        return "Leadership with beta"
    if transmission_gap_pct < 0 and beta_now >= 0.3 and beta_roc >= 0:
        return "Lagging high-beta"
    if transmission_gap_pct >= 0 and beta_roc < 0:
        return "Independent strength"
    if transmission_gap_pct >= 0 and beta_now < 0:
        return "Countertrend hedge"
    return "Transition"



def scan_daily_movers(api: AlpacaAPI, symbols: list[str] | None = None) -> pd.DataFrame:
    universe = symbols or DEFAULT_UNIVERSE
    snapshots = api.get_snapshots(universe, feed="iex")

    rows = []
    for sym, blob in snapshots.items():
        daily = (blob or {}).get("dailyBar") or {}
        prev = (blob or {}).get("prevDailyBar") or {}

        close = pd.to_numeric(daily.get("c"), errors="coerce")
        prev_close = pd.to_numeric(prev.get("c"), errors="coerce")
        if pd.isna(close) or pd.isna(prev_close) or prev_close == 0:
            continue

        pct = ((close / prev_close) - 1.0) * 100.0
        rows.append(
            {
                "symbol": sym,
                "close": close,
                "prev_close": prev_close,
                "change_pct": pct,
                "volume": pd.to_numeric(daily.get("v"), errors="coerce"),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    return out.sort_values("change_pct", ascending=False)



def load_price_history(api: AlpacaAPI, symbol: str, days: int = 365) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    normalized_symbol = AlpacaAPI._normalize_symbol(symbol)
    bars = api.get_stock_bars([normalized_symbol], start=start, end=end, timeframe="1Day", feed="iex")
    frame = bars.get(normalized_symbol, pd.DataFrame())
    if frame.empty:
        return frame
    keep = [c for c in ["timestamp", "open", "high", "low", "close", "volume"] if c in frame.columns]
    return frame[keep].dropna(subset=["timestamp", "close"]).sort_values("timestamp")


def build_momentum_profiles_from_bars(
    bars: dict[str, pd.DataFrame],
    *,
    symbols: list[str] | None = None,
) -> pd.DataFrame:
    universe = symbols or DEFAULT_UNIVERSE
    rows: list[dict[str, float | str]] = []
    for symbol in universe:
        normalized_symbol = AlpacaAPI._normalize_symbol(symbol)
        frame = bars.get(normalized_symbol, pd.DataFrame())
        if frame.empty or "close" not in frame.columns:
            continue

        close = pd.to_numeric(frame["close"], errors="coerce").dropna()
        if len(close) < 63:
            continue

        slope_1w = _log_slope(close, 5)
        slope_1m = _log_slope(close, 21)
        slope_3m = _log_slope(close, 63)
        trend_r2_3m = _trend_r2(close, 63)
        if pd.isna(slope_1m) or pd.isna(slope_3m):
            continue

        roc_1w_to_1m = _ratio_minus_one(slope_1m, slope_1w)
        roc_1m_to_3m = _ratio_minus_one(slope_3m, slope_1m)

        rows.append(
            {
                "symbol": normalized_symbol,
                "close": float(close.iloc[-1]),
                "return_1d_pct": _window_return_pct(close, 2),
                "return_7d_pct": _window_return_pct(close, 7),
                "daily_change_pct": _window_return_pct(close, 2),
                "return_1w_pct": _window_return_pct(close, 5),
                "return_1m_pct": _window_return_pct(close, 21),
                "return_3m_pct": _window_return_pct(close, 63),
                "return_1y_pct": _window_return_pct(close, 252),
                "return_5y_pct": _window_return_pct(close, 1260),
                "momentum_1w": slope_1w,
                "momentum_1m": slope_1m,
                "momentum_3m": slope_3m,
                "roc_1w_to_1m": roc_1w_to_1m,
                "roc_1m_to_3m": roc_1m_to_3m,
                "trend_r2_3m": trend_r2_3m,
                "trend_fit_gap": (1.0 - trend_r2_3m) if pd.notna(trend_r2_3m) else np.nan,
                "sparkline_3m": _sparkline(close, 63),
                "momentum_score": slope_3m,
                "momentum_roc_score": _mean_finite([roc_1w_to_1m, roc_1m_to_3m]),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    numeric_cols = [col for col in out.columns if col not in {"symbol", "sparkline_3m"}]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.sort_values(["momentum_score", "momentum_roc_score"], ascending=False, na_position="last").reset_index(drop=True)


def scan_momentum_profiles(
    api: AlpacaAPI,
    symbols: list[str] | None = None,
    days: int = 3650,
) -> pd.DataFrame:
    universe = [AlpacaAPI._normalize_symbol(symbol) for symbol in (symbols or DEFAULT_UNIVERSE) if str(symbol).strip()]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    bars = api.get_stock_bars(universe, start=start, end=end, timeframe="1Day", feed="iex")
    return build_momentum_profiles_from_bars(bars, symbols=universe)


def build_correlation_phase_shifts_from_bars(
    bars: dict[str, pd.DataFrame],
    *,
    symbols: list[str] | None = None,
    benchmark: str = "SPY",
    days: int = 252,
    corr_window: int = 20,
    roc_window: int = 10,
    momentum_window: int = 63,
) -> dict[str, pd.DataFrame | str]:
    universe = [AlpacaAPI._normalize_symbol(symbol) for symbol in (symbols or DEFAULT_UNIVERSE) if str(symbol).strip()]
    benchmark_symbol = AlpacaAPI._normalize_symbol(benchmark or "SPY")
    universe = [symbol for symbol in universe if symbol != benchmark_symbol]
    if not universe:
        return {"summary": pd.DataFrame(), "history": pd.DataFrame(), "benchmark": benchmark_symbol}

    benchmark_frame = bars.get(benchmark_symbol, pd.DataFrame())
    if benchmark_frame.empty or "close" not in benchmark_frame.columns:
        return {"summary": pd.DataFrame(), "history": pd.DataFrame(), "benchmark": benchmark_symbol}

    benchmark_frame = benchmark_frame.copy()
    if "timestamp" in benchmark_frame.columns:
        benchmark_frame["timestamp"] = pd.to_datetime(benchmark_frame["timestamp"], utc=True, errors="coerce")
    history_days = max(int(days), momentum_window + corr_window + roc_window + 30)
    cutoff = None
    benchmark_end = pd.to_datetime(benchmark_frame.get("timestamp"), utc=True, errors="coerce").dropna().max()
    if pd.notna(benchmark_end):
        cutoff = benchmark_end - pd.Timedelta(days=history_days)
        benchmark_frame = benchmark_frame[benchmark_frame["timestamp"] >= cutoff].copy()

    benchmark_price = (
        benchmark_frame[["timestamp", "close"]]
        .dropna(subset=["timestamp", "close"])
        .sort_values("timestamp")
        .rename(columns={"close": "benchmark_close"})
    )
    if benchmark_price.empty:
        return {"summary": pd.DataFrame(), "history": pd.DataFrame(), "benchmark": benchmark_symbol}
    benchmark_price["benchmark_return"] = pd.to_numeric(benchmark_price["benchmark_close"], errors="coerce").pct_change()
    benchmark_price["benchmark_norm"] = benchmark_price["benchmark_close"] / float(benchmark_price["benchmark_close"].iloc[0])

    history_rows: list[pd.DataFrame] = []
    summary_rows: list[dict[str, object]] = []
    for symbol in universe:
        frame = bars.get(symbol, pd.DataFrame())
        if frame.empty or "close" not in frame.columns:
            continue

        frame = frame.copy()
        if "timestamp" in frame.columns:
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        if cutoff is not None:
            frame = frame[frame["timestamp"] >= cutoff].copy()

        asset_price = (
            frame[["timestamp", "close"]]
            .dropna(subset=["timestamp", "close"])
            .sort_values("timestamp")
            .rename(columns={"close": "close"})
        )
        merged = benchmark_price.merge(asset_price, on="timestamp", how="inner")
        if len(merged) < max(corr_window + roc_window + 5, momentum_window + roc_window + 5):
            continue

        merged["asset_return"] = pd.to_numeric(merged["close"], errors="coerce").pct_change()
        merged["asset_norm"] = merged["close"] / float(merged["close"].iloc[0])
        merged = merged.dropna(subset=["benchmark_return", "asset_return"]).reset_index(drop=True)
        if len(merged) < max(corr_window + roc_window + 3, momentum_window + roc_window + 3):
            continue

        merged["rolling_correlation"] = merged["asset_return"].rolling(corr_window).corr(merged["benchmark_return"])
        merged["correlation_roc"] = merged["rolling_correlation"].diff(roc_window)
        merged["compound_1m"] = _rolling_compound(merged["asset_return"], max(21, corr_window))
        merged["compound_3m"] = _rolling_compound(merged["asset_return"], momentum_window)
        merged["compounding_momentum"] = (
            (1.0 + merged["compound_1m"].clip(lower=-0.99))
            * (1.0 + merged["compound_3m"].clip(lower=-0.99))
            - 1.0
        )
        merged["momentum_roc"] = merged["compounding_momentum"].diff(roc_window)
        merged["symbol"] = symbol
        merged["benchmark"] = benchmark_symbol

        history_rows.append(
            merged[
                [
                    "timestamp",
                    "symbol",
                    "benchmark",
                    "close",
                    "benchmark_close",
                    "asset_norm",
                    "benchmark_norm",
                    "rolling_correlation",
                    "correlation_roc",
                    "compound_1m",
                    "compound_3m",
                    "compounding_momentum",
                    "momentum_roc",
                ]
            ].copy()
        )

        latest = merged.iloc[-1]
        summary_rows.append(
            {
                "symbol": symbol,
                "benchmark": benchmark_symbol,
                "close": float(latest["close"]),
                "correlation_now": float(latest["rolling_correlation"]) if pd.notna(latest["rolling_correlation"]) else np.nan,
                "correlation_roc": float(latest["correlation_roc"]) if pd.notna(latest["correlation_roc"]) else np.nan,
                "compound_1m_pct": float(latest["compound_1m"] * 100.0) if pd.notna(latest["compound_1m"]) else np.nan,
                "compound_3m_pct": float(latest["compound_3m"] * 100.0) if pd.notna(latest["compound_3m"]) else np.nan,
                "compounding_momentum_pct": (
                    float(latest["compounding_momentum"] * 100.0) if pd.notna(latest["compounding_momentum"]) else np.nan
                ),
                "momentum_roc_pct": float(latest["momentum_roc"] * 100.0) if pd.notna(latest["momentum_roc"]) else np.nan,
            }
        )

    history = pd.concat(history_rows, ignore_index=True) if history_rows else pd.DataFrame()
    summary = pd.DataFrame(summary_rows)
    if summary.empty:
        return {"summary": summary, "history": history, "benchmark": benchmark_symbol}

    corr_roc_z = _zscore(summary["correlation_roc"])
    comp_mom_z = _zscore(summary["compounding_momentum_pct"])
    mom_roc_z = _zscore(summary["momentum_roc_pct"])
    corr_now_z = _zscore(summary["correlation_now"])

    summary["decoupling_score"] = (comp_mom_z * 0.45 + mom_roc_z * 0.35 - corr_roc_z * 0.20) * 100.0
    summary["beta_breakout_score"] = (comp_mom_z * 0.40 + mom_roc_z * 0.25 + corr_roc_z * 0.25 + corr_now_z * 0.10) * 100.0
    summary["correlation_break_score"] = (corr_roc_z.abs() * 0.65 + mom_roc_z.abs() * 0.35) * 100.0
    summary["phase_regime"] = [
        _phase_regime(comp_mom, mom_roc, corr_roc)
        for comp_mom, mom_roc, corr_roc in zip(
            summary["compounding_momentum_pct"],
            summary["momentum_roc_pct"],
            summary["correlation_roc"],
        )
    ]
    summary = summary.sort_values(["decoupling_score", "beta_breakout_score"], ascending=False, na_position="last").reset_index(drop=True)
    return {"summary": summary, "history": history, "benchmark": benchmark_symbol}


def scan_correlation_phase_shifts(
    api: AlpacaAPI,
    symbols: list[str] | None = None,
    benchmark: str = "SPY",
    days: int = 252,
    corr_window: int = 20,
    roc_window: int = 10,
    momentum_window: int = 63,
) -> dict[str, pd.DataFrame | str]:
    universe = [AlpacaAPI._normalize_symbol(symbol) for symbol in (symbols or DEFAULT_UNIVERSE) if str(symbol).strip()]
    benchmark_symbol = AlpacaAPI._normalize_symbol(benchmark or "SPY")
    universe = [symbol for symbol in universe if symbol != benchmark_symbol]
    if not universe:
        return {"summary": pd.DataFrame(), "history": pd.DataFrame(), "benchmark": benchmark_symbol}

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(int(days), momentum_window + corr_window + roc_window + 30))
    bars = api.get_stock_bars(
        [benchmark_symbol] + universe,
        start=start,
        end=end,
        timeframe="1Day",
        feed="iex",
    )
    return build_correlation_phase_shifts_from_bars(
        bars,
        symbols=universe,
        benchmark=benchmark_symbol,
        days=days,
        corr_window=corr_window,
        roc_window=roc_window,
        momentum_window=momentum_window,
    )


def scan_commodity_regimes(
    api: AlpacaAPI,
    symbols: list[str] | None = None,
    commodity_symbols: list[str] | None = None,
    days: int = 252,
    corr_window: int = 20,
    roc_window: int = 10,
    momentum_window: int = 63,
) -> dict[str, pd.DataFrame]:
    universe = _unique_symbols(symbols or COMMODITY_FOCUS_UNIVERSES["Broad Commodity Market"])
    commodity_universe = _unique_symbols(commodity_symbols or commodity_reference_universe())
    if not universe or not commodity_universe:
        return {"summary": pd.DataFrame(), "history": pd.DataFrame()}

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(int(days), momentum_window + corr_window + roc_window + 30))
    bars = api.get_stock_bars(
        _unique_symbols(universe + commodity_universe),
        start=start,
        end=end,
        timeframe="1Day",
        feed="iex",
    )

    prepared_prices: dict[str, pd.DataFrame] = {}
    commodity_frames: list[pd.DataFrame] = []
    for symbol in _unique_symbols(universe + commodity_universe):
        frame = bars.get(symbol, pd.DataFrame())
        if frame.empty or "close" not in frame.columns:
            continue
        prepared = (
            frame[["timestamp", "close"]]
            .dropna(subset=["timestamp", "close"])
            .sort_values("timestamp")
        )
        prepared["close"] = pd.to_numeric(prepared["close"], errors="coerce")
        prepared = prepared.dropna(subset=["timestamp", "close"]).reset_index(drop=True)
        if prepared.empty:
            continue
        prepared_prices[symbol] = prepared
        if symbol in commodity_universe:
            commodity_frame = prepared.rename(columns={"close": symbol})
            commodity_frames.append(commodity_frame)

    if not commodity_frames:
        return {"summary": pd.DataFrame(), "history": pd.DataFrame()}

    commodity_close = commodity_frames[0]
    for frame in commodity_frames[1:]:
        commodity_close = commodity_close.merge(frame, on="timestamp", how="outer")
    commodity_close = commodity_close.sort_values("timestamp")
    commodity_matrix = commodity_close.set_index("timestamp")
    for col in commodity_matrix.columns:
        commodity_matrix[col] = pd.to_numeric(commodity_matrix[col], errors="coerce")
    normalized_components = _normalized_price_matrix(commodity_matrix)
    available_reference = [symbol for symbol in commodity_universe if symbol in normalized_components.columns]
    if not available_reference:
        return {"summary": pd.DataFrame(), "history": pd.DataFrame()}

    history_rows: list[pd.DataFrame] = []
    summary_rows: list[dict[str, object]] = []
    required_length = max(corr_window + roc_window + 5, momentum_window + roc_window + 5)
    for symbol in universe:
        asset_price = prepared_prices.get(symbol)
        if asset_price is None or asset_price.empty:
            continue

        reference_components = [ref for ref in available_reference if ref != symbol]
        if not reference_components:
            reference_components = list(available_reference)
        commodity_basket = _build_equal_weight_basket(normalized_components[reference_components])
        if len(commodity_basket) < required_length:
            continue

        merged = commodity_basket.merge(asset_price, on="timestamp", how="inner")
        if len(merged) < required_length:
            continue

        merged["asset_return"] = merged["close"].pct_change()
        merged["asset_norm"] = merged["close"] / float(merged["close"].iloc[0])
        merged = merged.dropna(subset=["commodity_return", "asset_return"]).reset_index(drop=True)
        if len(merged) < required_length:
            continue

        merged["rolling_correlation"] = merged["asset_return"].rolling(corr_window).corr(merged["commodity_return"])
        merged["correlation_roc"] = merged["rolling_correlation"].diff(roc_window)
        merged["rolling_beta"] = _rolling_beta(merged["asset_return"], merged["commodity_return"], corr_window)
        merged["beta_roc"] = merged["rolling_beta"].diff(roc_window)
        merged["asset_compounding_momentum"] = _rolling_compound(merged["asset_return"], momentum_window)
        merged["commodity_compounding_momentum"] = _rolling_compound(merged["commodity_return"], momentum_window)
        merged["transmission_gap"] = merged["asset_compounding_momentum"] - merged["commodity_compounding_momentum"]
        merged["relative_strength"] = (merged["asset_norm"] / merged["commodity_norm"]) - 1.0
        merged["rolling_high"] = pd.to_numeric(merged["close"], errors="coerce").rolling(
            max(momentum_window, 63),
            min_periods=min(20, max(momentum_window, 63)),
        ).max()
        merged["pullback_from_high"] = (merged["close"] / merged["rolling_high"]) - 1.0
        merged["symbol"] = symbol
        profile = commodity_proxy_profile(symbol)
        merged["name"] = profile["name"]
        merged["commodity_label"] = profile["commodity"]

        history_rows.append(
            merged[
                [
                    "timestamp",
                    "symbol",
                    "name",
                    "commodity_label",
                    "close",
                    "commodity_close",
                    "asset_norm",
                    "commodity_norm",
                    "rolling_correlation",
                    "correlation_roc",
                    "rolling_beta",
                    "beta_roc",
                    "asset_compounding_momentum",
                    "commodity_compounding_momentum",
                    "transmission_gap",
                    "relative_strength",
                    "pullback_from_high",
                ]
            ].copy()
        )

        latest = merged.iloc[-1]
        summary_rows.append(
            {
                "symbol": symbol,
                "name": profile["name"],
                "commodity_label": profile["commodity"],
                "description": profile["description"],
                "close": float(latest["close"]),
                "correlation_now": float(latest["rolling_correlation"]) if pd.notna(latest["rolling_correlation"]) else np.nan,
                "correlation_roc": float(latest["correlation_roc"]) if pd.notna(latest["correlation_roc"]) else np.nan,
                "beta_now": float(latest["rolling_beta"]) if pd.notna(latest["rolling_beta"]) else np.nan,
                "beta_roc": float(latest["beta_roc"]) if pd.notna(latest["beta_roc"]) else np.nan,
                "asset_compounding_momentum_pct": (
                    float(latest["asset_compounding_momentum"] * 100.0)
                    if pd.notna(latest["asset_compounding_momentum"])
                    else np.nan
                ),
                "commodity_compounding_momentum_pct": (
                    float(latest["commodity_compounding_momentum"] * 100.0)
                    if pd.notna(latest["commodity_compounding_momentum"])
                    else np.nan
                ),
                "transmission_gap_pct": float(latest["transmission_gap"] * 100.0) if pd.notna(latest["transmission_gap"]) else np.nan,
                "relative_strength_pct": float(latest["relative_strength"] * 100.0) if pd.notna(latest["relative_strength"]) else np.nan,
                "pullback_from_high_pct": float(latest["pullback_from_high"] * 100.0) if pd.notna(latest["pullback_from_high"]) else np.nan,
                "reference_basket": ", ".join(reference_components),
            }
        )

    history = pd.concat(history_rows, ignore_index=True) if history_rows else pd.DataFrame()
    summary = pd.DataFrame(summary_rows)
    if summary.empty:
        return {"summary": summary, "history": history}

    gap_z = _zscore(summary["transmission_gap_pct"])
    beta_z = _zscore(summary["beta_now"])
    beta_roc_z = _zscore(summary["beta_roc"])
    rel_strength_z = _zscore(summary["relative_strength_pct"])
    asset_momentum_z = _zscore(summary["asset_compounding_momentum_pct"])

    summary["beneficiary_score"] = (gap_z * 0.40 + rel_strength_z * 0.25 + beta_z * 0.20 + beta_roc_z * 0.15) * 100.0
    summary["squeeze_score"] = ((-gap_z) * 0.35 + beta_z * 0.25 + beta_roc_z * 0.20 + (-asset_momentum_z) * 0.20) * 100.0
    summary["decoupler_score"] = (gap_z * 0.35 + rel_strength_z * 0.25 + (-beta_z) * 0.25 + (-beta_roc_z) * 0.15) * 100.0
    summary["commodity_regime"] = [
        _commodity_regime(gap, beta_now, beta_roc)
        for gap, beta_now, beta_roc in zip(
            summary["transmission_gap_pct"],
            summary["beta_now"],
            summary["beta_roc"],
        )
    ]
    summary = summary.sort_values(["beneficiary_score", "decoupler_score"], ascending=False, na_position="last").reset_index(drop=True)
    return {"summary": summary, "history": history}
