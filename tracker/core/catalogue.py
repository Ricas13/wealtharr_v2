"""Read-only, versioned catalogue: which exact fund each strategy role uses.

Broker-neutral. A fund *listing* (one exchange trading line: ISIN, ticker,
quote unit) carries its own evidence for each broker known to carry it. A
strategy role names its listings in order of preference; for a broker and
account type the first listing with evidence for that broker is used. No
evidence, no offer: a broker is never assumed to carry a fund.

The account check is a deliberate hold. Issuer ISA eligibility and a public
broker listing do not prove this owner's buying permission.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from tracker.core.brokers import BROKERS, broker_name
from tracker.instrument_mapping import (
    EQUITY_UK, SAFE_UK, MAPPING_VERSION, STRATEGY_9SIG,
    enrolment_manifest,
)
from tracker.strategies.registry import describe_strategy


CATALOGUE_VERSION = MAPPING_VERSION
ALL_WEATHER = "all-weather"
GOLDEN_BUTTERFLY = "golden-butterfly"
DUAL_MOMENTUM = "dual-momentum"
PERMANENT_PORTFOLIO = "permanent-portfolio"
HFEA = "hfea"
THREE_SIG = "three-sig"
NASDAQ_TREND_DCA = "nasdaq-trend-dca"
NASDAQ_TREND_DCA = "nasdaq-trend-dca"
from tracker.core.brokers import ACCOUNT_TYPE_NAMES  # noqa: E402
ACCOUNT_TYPES = tuple(ACCOUNT_TYPE_NAMES.items())


@dataclass(frozen=True)
class StrategyOffer:
    key: str
    title: str
    risk: str
    cadence: str
    explanation: str
    enabled: bool


_OFFERS = {
    STRATEGY_9SIG: StrategyOffer(
        STRATEGY_9SIG, "9Sig", "High risk", "Quarterly",
        "A leveraged Nasdaq-100 growth sleeve with a defensive sleeve. "
        "Quarterly decisions use the versioned 9Sig rules.", True,
    ),
    NASDAQ_TREND_DCA: StrategyOffer(
        NASDAQ_TREND_DCA, "3× Nasdaq Trend + DCA", "Very high risk", "Daily",
        "SPY 200-day trend with 3× Nasdaq, latched QQQ safeguards and 12-month "
        "cash-funded DCA into unleveraged Nasdaq. All orders remain manual.", True,
    ),
    ALL_WEATHER: StrategyOffer(
        ALL_WEATHER, "All Seasons", "Moderate risk", "Yearly",
        "Five funds at the published All Seasons weights (Tony Robbins' retail version of Ray "
        "Dalio's ideas, not Bridgewater's All Weather fund): stocks, long-term and intermediate "
        "government bonds, gold and commodities, rebalanced once a year. Not affiliated with "
        "Robbins, Dalio or Bridgewater.", True,
    ),
    GOLDEN_BUTTERFLY: StrategyOffer(
        GOLDEN_BUTTERFLY, "Golden Butterfly", "Moderate risk", "Yearly",
        "Five equal parts: stocks, small-cap value, long-term and short-term government bonds, "
        "and gold, rebalanced once a year. Not affiliated with Portfolio Charts.", True,
    ),
    DUAL_MOMENTUM: StrategyOffer(
        DUAL_MOMENTUM, "Dual Momentum", "High risk", "Monthly",
        "100% in one fund, chosen each month: US shares, world ex-US shares, or bonds when shares "
        "aren't beating cash. Not affiliated with Gary Antonacci.", True,
    ),
    PERMANENT_PORTFOLIO: StrategyOffer(
        PERMANENT_PORTFOLIO, "Permanent Portfolio", "Low risk", "Monthly check",
        "25% each in stocks, long-term government bonds, cash and gold, rebalanced only when a "
        "part drifts below 15% or above 35%.", True,
    ),
    HFEA: StrategyOffer(
        HFEA, "HFEA", "Very high risk", "Quarterly",
        "55% 3x S&P 500 and 45% 3x long-term US Treasuries, rebalanced every quarter "
        "(HEDGEFUNDIE's Excellent Adventure). Not affiliated with HEDGEFUNDIE or Bogleheads.", True,
    ),
    NASDAQ_TREND_DCA: StrategyOffer(
        NASDAQ_TREND_DCA, "3× Nasdaq Trend + DCA", "Very high risk", "Daily",
        "Use SPY's 200-day moving average to switch between 3x Nasdaq, Nasdaq, "
        "and cash; after an exit, invest confirmed sale proceeds in 12 monthly "
        "unleveraged Nasdaq instalments. No broker trades are placed.", False,
    ),
    THREE_SIG: StrategyOffer(
        THREE_SIG, "3Sig", "High risk", "Quarterly",
        "80% small-company shares, 20% bonds; each quarter the share side is steered to a 3% growth "
        "target (Jason Kelly's 3% Signal). Not affiliated with Jason Kelly or The Kelly Letter.", True,
    ),
}
OFFERS = MappingProxyType(_OFFERS)


@dataclass(frozen=True)
class Listing:
    """One exchange trading line of one fund, with per-broker evidence."""
    name: str
    issuer: str
    isin: str
    ticker: str  # Yahoo symbol of this exact line
    raw_quote_unit: str  # GBX (pence), GBP, or USD (converted at that day's GBP/USD close)
    trading_line: str
    issuer_url: str
    note: str
    verified_on: date
    brokers: dict = field(default_factory=dict)  # broker key -> evidence URL
    wrappers: tuple = ("ISA", "GIA")  # account types the issuer says it suits
    country: str = "GB"
    currency: str = "GBP"  # the account currency this line is tracked in


@dataclass(frozen=True)
class CatalogueLine:
    strategy_key: str
    role: str
    country: str
    platform: str
    account_type: str
    mapping_version: str
    name: str
    ticker: str
    isin: str
    raw_quote_unit: str
    currency: str
    trading_line: str
    weight: Decimal
    status: str  # verified, account_check_pending, draft
    issuer_url: str = ""
    broker_evidence_url: str = ""
    verified_on: date | None = None
    note: str = ""
    issuer: str = ""

    @property
    def role_label(self):
        return {
            "growth": "Growth", "safe": "Safe",
            "stocks": "Equities", "long": "Long bonds",
            "inter": "Intermediate bonds", "gold": "Gold",
            "comm": "Commodities",
            "scv": "Small-cap value", "short": "Short bonds",
    "us": "US shares", "intl": "World ex-US shares", "cashfund": "Cash fund", "bonds": "Bonds",
            "stocks3x": "3x US shares", "bonds3x": "3x Treasuries",
            "smallcap": "Small-company shares",
        }.get(self.role, self.role)

    @property
    def weight_pct(self):
        return self.weight * Decimal("100")

    @property
    def broker_name(self):
        return broker_name(self.platform)


ACCOUNT_HOLD = ("Your own account's access and the live price quote unit are NOT confirmed. "
                "A different unit from Yahoo holds the price rather than guessing.")


def _from_instrument(item):
    return Listing(
        item.name, item.issuer, item.isin, item.yahoo_ticker, item.raw_quote_unit,
        item.trading_line, item.issuer_isa_evidence_url or item.issuer_url,
        "Issuer and public broker listing reviewed. " + ACCOUNT_HOLD,
        item.verified_on, brokers={"TRADING212": item.broker_evidence_url})


T212 = "https://www.trading212.com/trading-instruments/invest/%s.GB"
AW_VERIFIED_ON = date(2026, 10, 21)


def _aw(name, issuer, isin, ticker, unit, line, issuer_url, note):
    return Listing(name, issuer, isin, ticker, unit, line, issuer_url,
                   note + " Source-cited, not read first-hand. " + ACCOUNT_HOLD,
                   AW_VERIFIED_ON, brokers={"TRADING212": T212 % ticker.split(".")[0]})


VUAG = _aw("Vanguard S&P 500 UCITS ETF (USD) Accumulating", "Vanguard",
           "IE00BFMXXD54", "VUAG.L", "GBP", "LSE VUAG (GBP line)",
           "https://www.vanguard.co.uk/uk-fund-directory/product/etf/equity/9694/sp-500-ucits-etf-usd-accumulating",
           "US shares in USD, unhedged, on a GBP line.")
GLTL = _aw("SPDR Bloomberg 15+ Year Gilt UCITS ETF (Dist)", "State Street",
           "IE00B6YX5L24", "GLTL.L", "GBP", "LSE GLTL (GBP line)",
           "https://www.ssga.com/uk/en_gb/intermediary/etfs/spdr-bloomberg-15-year-gilt-ucits-etf-dist-sybl-gy",
           "UK equivalent of long-term US Treasuries. Distributing; small fund.")
IGLT = _aw("iShares Core UK Gilts UCITS ETF (Dist)", "iShares",
           "IE00B1FZSB30", "IGLT.L", "GBP", "LSE IGLT (GBP line)",
           "https://www.ishares.com/uk/individual/en/products/251806",
           "UK equivalent of intermediate US Treasuries. All-maturity gilts: no 5-15 year gilt fund "
           "was found at the broker, so it overlaps the long role.")
SGLN = _aw("iShares Physical Gold ETC", "iShares",
           "IE00B4ND3602", "SGLN.L", "GBX", "LSE SGLN (pence line)",
           "https://www.ishares.com/uk/individual/en/products/258441",
           "An ETC (secured debt security), not a UCITS fund; physically backed; unhedged.")
CMOP = _aw("Invesco Bloomberg Commodity UCITS ETF (Acc)", "Invesco",
           "IE00BD6FTQ80", "CMOP.L", "GBX", "LSE CMOP (pence line)",
           "https://www.invesco.com/content/dam/invesco/uk/en/product-documents/etf/share-class/factsheet/IE00BD6FTQ80_factsheet_en-uk.pdf",
           "Swap-based (synthetic); broad commodities including precious metals.")

GB_VERIFIED_ON = date(2026, 10, 21)
USSC = Listing(
    "SPDR MSCI USA Small Cap Value Weighted UCITS ETF (Acc)", "State Street",
    "IE00BSPLC413", "USSC.L", "USD", "LSE USSC (USD line)",
    "https://www.ssga.com/uk/en_gb/intermediary/etfs/funds/spdr-msci-usa-small-cap-value-weighted-ucits-etf-zprv-gy",
    "US small-cap value, the only such index fund found on the London market. Quoted in US dollars: "
    "Wealtharr converts each close at that day's GBP/USD close, and your broker charges its own "
    "currency fee. Record fills in pounds (total paid divided by shares). Source-cited, not read "
    "first-hand. " + ACCOUNT_HOLD,
    GB_VERIFIED_ON, brokers={"TRADING212": T212 % "USSC"})
IGLS = Listing(
    "iShares UK Gilts 0-5yr UCITS ETF (Dist)", "iShares",
    "IE00B4WXJK79", "IGLS.L", "GBP", "LSE IGLS (GBP line)",
    "https://www.ishares.com/gls-download/literature/fact-sheet/igls-ishares-uk-gilts-0-5yr-ucits-etf-fund-fact-sheet-en-gb.pdf",
    "UK equivalent of short-term US Treasuries: gilts up to 5 years. Distributing. Source-cited, "
    "not read first-hand. " + ACCOUNT_HOLD,
    GB_VERIFIED_ON, brokers={"TRADING212": T212 % "IGLS"})

# Dual Momentum. Trading 212 pages for XUSE and VAGS weren't found by search; the
# owner confirmed both lines are on Trading 212 (21 Oct 2026).
T212_OWNER_CONFIRMED = " Owner confirmed it is listed on Trading 212 (21 Oct 2026)."
XUSE = Listing(
    "iShares MSCI World ex-USA UCITS ETF (Acc)", "iShares",
    "IE000R4ZNTN3", "XUSE.L", "GBP", "LSE XUSE (GBP line)",
    "https://www.blackrock.com/uk/individual/products/340748/ishares-msci-world-ex-usa-ucits-etf",
    "UK equivalent of MSCI ACWI ex-US: developed markets only (no emerging markets; no ACWI ex-US "
    "UCITS fund was found). Accumulating, so its price is a total return. Listed April 2025. "
    "Source-cited, not read first-hand." + T212_OWNER_CONFIRMED + " " + ACCOUNT_HOLD,
    GB_VERIFIED_ON, brokers={"TRADING212": T212 % "XUSE"})
VAGS = Listing(
    "Vanguard Global Aggregate Bond UCITS ETF GBP Hedged (Acc)", "Vanguard",
    "IE00BG47K971", "VAGS.L", "GBP", "LSE VAGS (GBP line)",
    "https://www.vanguard.co.uk/professional/product/etf/bond/9685/global-aggregate-bond-ucits-etf-hedged-accumulating",
    "UK equivalent of US aggregate bonds: global aggregate bonds hedged to pounds. Accumulating. "
    "Source-cited, not read first-hand." + T212_OWNER_CONFIRMED + " " + ACCOUNT_HOLD,
    GB_VERIFIED_ON, brokers={"TRADING212": T212 % "VAGS"})

# United States, Fidelity. The original US funds; prices in dollars, no
# conversion. Fidelity lists US ETFs commission-free; its quote page per ticker
# is the broker evidence (pattern confirmed for some tickers, not read first-hand).
US_VERIFIED_ON = date(2026, 10, 21)
FIDELITY = "https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol=%s"
US_WRAPPERS = ("BROKERAGE", "TRAD_IRA", "ROTH_IRA")


def _us(name, issuer, isin, ticker, issuer_url, note):
    return Listing(name, issuer, isin, ticker, "USD", "%s (USD)" % ticker, issuer_url,
                   note + " Source-cited, not read first-hand. " + ACCOUNT_HOLD, US_VERIFIED_ON,
                   brokers={"FIDELITY": FIDELITY % ticker}, wrappers=US_WRAPPERS,
                   country="US", currency="USD")


US_VTI = _us("Vanguard Total Stock Market ETF", "Vanguard", "US9229087690", "VTI",
             "https://investor.vanguard.com/investment-products/etfs/profile/vti", "US total stock market.")
US_TLT = _us("iShares 20+ Year Treasury Bond ETF", "iShares", "US4642874329", "TLT",
             "https://www.ishares.com/us/products/239454/", "Long-term US Treasuries.")
US_IEF = _us("iShares 7-10 Year Treasury Bond ETF", "iShares", "US4642874402", "IEF",
             "https://www.ishares.com/us/products/239456/", "Intermediate US Treasuries.")
US_GLD = _us("SPDR Gold Shares", "State Street", "US78463V1070", "GLD",
             "https://www.ssga.com/us/en/individual/etfs/spdr-gold-shares-gld", "Physical gold (a grantor trust).")
US_DBC = _us("Invesco DB Commodity Index Tracking Fund", "Invesco", "US46138B1035", "DBC",
             "https://www.invesco.com/us/financial-products/etfs/product-detail?productId=DBC",
             "Broad commodities. A commodity pool: it issues a Schedule K-1 for taxes.")
US_VBR = _us("Vanguard Small-Cap Value ETF", "Vanguard", "US9229086114", "VBR",
             "https://investor.vanguard.com/investment-products/etfs/profile/VBR", "US small-cap value.")
US_SHY = _us("iShares 1-3 Year Treasury Bond ETF", "iShares", "US4642874576", "SHY",
             "https://www.ishares.com/us/products/239452/", "Short-term US Treasuries.")
US_SPY = _us("SPDR S&P 500 ETF Trust", "State Street", "US78462F1030", "SPY",
             "https://www.ssga.com/us/en/individual/etfs/spdr-sp-500-etf-trust-spy", "The S&P 500, as in the original.")
US_ACWX = _us("iShares MSCI ACWI ex U.S. ETF", "iShares", "US4642882405", "ACWX",
              "https://www.ishares.com/us/products/239594/", "MSCI ACWI ex-US, as in the original.")
US_AGG = _us("iShares Core US Aggregate Bond ETF", "iShares", "US4642872265", "AGG",
             "https://www.ishares.com/us/products/239458/", "The US Aggregate bond index, as in the original.")

# Eurozone (Germany), Trade Republic. EUR lines on Xetra; local equivalents (euro
# government bonds for US Treasuries). Broker evidence is a Trade Republic page or a
# third-party listing naming the ISIN; lines without any are not offered there yet.
EU_VERIFIED_ON = date(2026, 10, 21)
TR_BROWSE = "https://app.traderepublic.com/browse/fund"
EU_WRAPPERS = ("DEPOT",)


def _eu(name, issuer, isin, ticker, issuer_url, note, evidence=""):
    return Listing(name, issuer, isin, ticker, "EUR", "Xetra %s (EUR line)" % ticker.split(".")[0],
                   issuer_url, note + " Source-cited, not read first-hand. " + ACCOUNT_HOLD,
                   EU_VERIFIED_ON, brokers={"TRADE_REPUBLIC": evidence} if evidence else {},
                   wrappers=EU_WRAPPERS, country="DE", currency="EUR")


EU_SXR8 = _eu("iShares Core S&P 500 UCITS ETF (Acc)", "iShares", "IE00B5BMR087", "SXR8.DE",
              "https://www.ishares.com/de/privatanleger/de/produkte/253743/", "US shares (S&P 500).",
              TR_BROWSE + "?q=IE00B5BMR087")
EU_DBXF = _eu("Xtrackers II Eurozone Government Bond 15-30 UCITS ETF 1C", "Xtrackers", "LU0290357507",
              "DBXF.DE", "https://live.deutsche-boerse.com/en/etf/xtrackers-ii-eurozone-government-bond-15-30-ucits-etf-1c",
              "Euro equivalent of long-term US Treasuries: 15-30 year eurozone government bonds. Small fund.",
              "https://www.onvista.de/etf/snapshot/LU0290357507")
EU_SXRQ = _eu("iShares € Govt Bond 7-10yr UCITS ETF (Acc)", "iShares", "IE00B3VTN290", "SXRQ.DE",
              "https://live.deutsche-boerse.com/etf/ishares-eur-govt-bond-7-10yr-ucits-etf-eur-acc",
              "Euro equivalent of intermediate US Treasuries: 7-10 year euro government bonds.",
              "https://www.finanzfluss.de/informer/etf/ie00b3vtn290/")
EU_PPFB = _eu("iShares Physical Gold ETC", "iShares", "IE00B4ND3602", "PPFB.DE",
              "https://www.ishares.com/uk/individual/en/products/258441",
              "Physical gold, an ETC (secured debt security), on its Xetra EUR line.",
              TR_BROWSE + "?q=IE00B4ND3602")
EU_SXRS = _eu("iShares Diversified Commodity Swap UCITS ETF", "iShares", "IE00BDFL4P12", "SXRS.DE",
              "https://www.comdirect.de/inf/etfs/IE00BDFL4P12",
              "Broad commodities (Bloomberg Commodity index), swap-based.",
              "https://www.brokerexperte.de/etf/ie00bdfl4p12/")
EU_ZPRV = _eu("SPDR MSCI USA Small Cap Value Weighted UCITS ETF (Acc)", "State Street", "IE00BSPLC413",
              "ZPRV.DE", "https://www.ssga.com/de/en_gb/intermediary/etfs/state-street-spdr-msci-usa-small-cap-value-weighted-ucits-etf-zprv-gy",
              "US small-cap value.",
              "https://www.finect.com/etfs/IE00BSPLC413-Spdr_msci_usa_small_cap_val_wtd_etf_usd")
EU_DBXP = _eu("Xtrackers II Eurozone Government Bond 1-3 UCITS ETF 1C", "Xtrackers", "LU0290356871", "DBXP.DE",
              "https://live.deutsche-boerse.com/en/etf/xtrackers-ii-eurozone-government-bond-1-3-ucits-etf-1c",
              "Euro equivalent of short-term US Treasuries: 1-3 year eurozone government bonds.",
              "https://www.onvista.de/etf/Xtrackers-II-Eurozone-Government-Bond-1-3-UCITS-ETF-1C-EUR-Acc-ETF-LU0290356871")
EU_EXUS = _eu("Xtrackers MSCI World ex USA UCITS ETF 1C", "Xtrackers", "IE0006WW1TQ4", "EXUS.DE",
              "https://finanzen.net/etf/xtrackers-msci-world-ex-usa-etf-1c-ie0006ww1tq4",
              "Euro-line equivalent of MSCI ACWI ex-US: developed markets only. Accumulating.",
              TR_BROWSE + "?q=IE0006WW1TQ4")
EU_VAGF = _eu("Vanguard Global Aggregate Bond UCITS ETF EUR Hedged (Acc)", "Vanguard", "IE00BG47KH54", "VAGF.DE",
              "https://live.deutsche-boerse.com/en/etf/vanguard-global-aggregate-bond-ucits-etf-eur-hedged-accumulating",
              "Euro equivalent of US aggregate bonds: global aggregate bonds hedged to euros. Accumulating.",
              TR_BROWSE + "?q=vanguard")

# 3× Nasdaq Trend + DCA: use the exact existing reviewed 9Sig LQQ3
# trading line. The DCA sleeve is a distributing UCITS ETF, so tracked cash
# dividends must not silently be treated as reinvested in performance.
UK_NASDAQ_1X = Listing(
    "Invesco EQQQ Nasdaq-100 UCITS ETF (Dist)", "Invesco", "IE0032077012",
    "EQQQ.L", "GBX", "LSE EQQQ (pence line)",
    "https://www.invesco.com/uk/en/financial-products/etfs/invesco-eqqq-nasdaq-100-ucits-etf-dist.html",
    "Physical Nasdaq-100 UCITS ETF, GBP trading line, quarterly cash dividends. "
    "The owner must confirm personal ISA permissions. " + ACCOUNT_HOLD,
    date(2026, 10, 23),
    brokers={"TRADING212": "https://www.trading212.com/trading-instruments/isa/EQQQ.GB"},
)

US_TQQQ = _us("ProShares UltraPro QQQ", "ProShares", "US74347X8314", "TQQQ",
              "https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq",
              "3x daily Nasdaq-100, Kelly's own 9Sig growth fund. Fidelity requires a leveraged-ETF "
              "agreement and the 'Most Aggressive' objective before buying; check its ETF service-fee list.")
EU_3QQQ = _eu("WisdomTree NASDAQ 100 3x Daily Leveraged", "WisdomTree", "IE00BLRPRL42", "3QQQ.DE",
              "https://www.wisdomtree.com/de/products/equities/wisdomtree-nasdaq-100-3x-daily-leveraged",
              "3x daily Nasdaq-100 on its Xetra EUR line: an ETP (collateralised note), not the US TQQQ fund. "
              "Trade Republic may ask knowledge and experience questions before buying.",
              "https://www.finect.com/etfs/IE00BLRPRL42-Wisdomtree_nasdaq_100_3x_daily_leveraged")
EU_ERNX = _eu("iShares € Ultrashort Bond UCITS ETF (Acc)", "iShares", "IE000RHYOR04", "ERNX.DE",
              "https://live.deutsche-boerse.com/en/etf/ishares-eur-ultrashort-bond-ucits-etf-eur-acc",
              "Euro ultrashort investment-grade bonds, the euro counterpart of the pound account's ERNS; "
              "not the US AGG bond fund.",
              "https://www.onvista.de/etf/iShares-EUR-Ultrashort-Bond-UCITS-ETF-Acc-ETF-IE000RHYOR04")

UK_ERNS = _from_instrument(SAFE_UK)
US_BIL = _us("SPDR Bloomberg 1-3 Month T-Bill ETF", "State Street", "US78468R6633", "BIL",
             "https://www.ssga.com/us/en/etfs/spdr-bloomberg-1-3-month-t-bill-etf-bil",
             "1-3 month US Treasury bills, Browne's cash.")
EU_XEON = _eu("Xtrackers II EUR Overnight Rate Swap UCITS ETF 1C", "Xtrackers", "LU0290358497", "XEON.DE",
              "https://live.deutsche-boerse.com/en/etf/xtrackers-ii-eur-overnight-rate-swap-ucits-etf-1c",
              "Euro equivalent of Treasury bills: the €STR overnight rate (swap-based), accumulating.",
              TR_BROWSE + "?q=LU0290358497")

# HFEA and 3Sig (22 Oct 2026). No Trading 212 instrument page was readable; the owner
# confirmed 3LUS/3USL, 3TYL and ISP6 are listed and buyable in their Trading 212 ISA.
T212_OWNER_CONFIRMED_22 = " Owner confirmed it is listed and buyable in their Trading 212 ISA (22 Oct 2026)."
HFEA_VERIFIED_ON = date(2026, 10, 22)
UK_3LUS = Listing(
    "WisdomTree S&P 500 3x Daily Leveraged", "WisdomTree", "IE00B7Y34M31", "3LUS.L", "GBX",
    "LSE 3LUS (pence line)",
    "https://www.wisdomtree.com/gb/products/equities/wisdomtree-sp-500-3x-daily-leveraged",
    "3x daily S&P 500 (net total return), the UK equivalent of UPRO: an ETP (collateralised note), "
    "not a fund. Issuer says ISA eligible. Daily leverage loses value in choppy markets. "
    "Source-cited, not read first-hand." + T212_OWNER_CONFIRMED_22 + " " + ACCOUNT_HOLD,
    HFEA_VERIFIED_ON, brokers={"TRADING212": T212 % "3LUS"})
UK_3TYL = Listing(
    "WisdomTree US Treasuries 10Y 3x Daily Leveraged", "WisdomTree", "IE00BKT09032", "3TYL.L", "GBX",
    "LSE 3TYL (pence line)",
    "https://www.wisdomtree.com/gb/products/fixed-income/wisdomtree-us-treasuries-10y-3x-daily-leveraged",
    "3x daily 10-year US Treasury futures: the closest European equivalent of TMF, which is 3x 20+ year "
    "Treasuries (none trades in Europe), so it reacts about 40% as strongly to rate changes. An ETP, "
    "very small. A dollar product, but this line is quoted in pence (owner-checked on Yahoo, 22 Oct 2026). "
    "Source-cited, not read first-hand."
    + T212_OWNER_CONFIRMED_22 + " " + ACCOUNT_HOLD,
    HFEA_VERIFIED_ON, brokers={"TRADING212": T212 % "3TYL"})
US_UPRO = _us("ProShares UltraPro S&P500", "ProShares", "US74347X8645", "UPRO",
              "https://www.proshares.com/our-etfs/leveraged-and-inverse/upro",
              "3x daily S&P 500, as in the original. Fidelity requires a leveraged-ETF agreement and the "
              "'Most Aggressive' objective before buying.")
US_TMF = _us("Direxion Daily 20+ Year Treasury Bull 3X Shares", "Direxion", "US25459W5402", "TMF",
             "https://www.direxion.com/product/daily-20-year-treasury-bull-bear-3x-etf",
             "3x daily 20+ year US Treasuries, as in the original. Fidelity requires a leveraged-ETF "
             "agreement before buying.")
UK_ISP6 = Listing(
    "iShares S&P SmallCap 600 UCITS ETF", "iShares", "IE00B2QWCY14", "ISP6.L", "GBX",
    "LSE ISP6 (pence line)",
    "https://www.ishares.com/uk/individual/en/products/251856/",
    "US small companies (S&P SmallCap 600), the same index as the book's IJR. A dollar fund, but this "
    "line is quoted in pence (owner-checked on Yahoo, 22 Oct 2026). Distributing; "
    "Wealtharr records dividends as cash. Source-cited, not read first-hand."
    + T212_OWNER_CONFIRMED_22 + " " + ACCOUNT_HOLD,
    HFEA_VERIFIED_ON, brokers={"TRADING212": T212 % "ISP6"})
US_IJR = _us("iShares Core S&P Small-Cap ETF", "iShares", "US4642878049", "IJR",
             "https://www.ishares.com/us/products/239774/",
             "US small companies (S&P SmallCap 600), the book's stock fund.")
EU_IUS3 = _eu("iShares S&P SmallCap 600 UCITS ETF", "iShares", "IE00B2QWCY14", "IUS3.DE",
              "https://www.ishares.com/de/privatanleger/de/produkte/251856/",
              "US small companies (S&P SmallCap 600), the same index as the book's IJR, on its Xetra EUR line.",
              "https://etf.capital/etf/IE00B2QWCY14")

# Nasdaq Trend + DCA: exact pound and US-dollar trading lines.
# UK listing/ISIN and GBX price unit: issuer and Trading 212 ISA catalogue.
UK_EQQQ = Listing(
    "Invesco EQQQ Nasdaq-100 UCITS ETF (Dist)", "Invesco", "IE0032077012",
    "EQQQ.L", "GBX", "LSE EQQQ (pence line)",
    "https://www.invesco.com/uk/en/financial-products/etfs/invesco-eqqq-nasdaq-100-ucits-etf-dist.html",
    "Physical, distributing UCITS ETF; paid distributions must be recorded as cash in the ledger. "
    "Its daily returns differ from QQQ. " + ACCOUNT_HOLD,
    date(2026, 10, 23),
    brokers={"TRADING212": "https://www.trading212.com/trading-instruments/isa/EQQQ.GB"},
)
US_QQQ = _us(
    "Invesco QQQ Trust", "Invesco", "US46090E1038", "QQQ",
    "https://www.invesco.com/qqq-etf/en/home.html",
    "Unleveraged Nasdaq-100 fund. QQQ distributions are tracked separately.",
)

# (strategy, role, country) -> listings, most preferred first.
ROLE_LISTINGS = {
    (NASDAQ_TREND_DCA, "leveraged", "GB"): (_from_instrument(EQUITY_UK),),
    (NASDAQ_TREND_DCA, "unleveraged", "GB"): (UK_NASDAQ_1X,),
    (NASDAQ_TREND_DCA, "leveraged", "GB"): (_from_instrument(EQUITY_UK),),
    (NASDAQ_TREND_DCA, "unleveraged", "GB"): (UK_EQQQ,),
    (NASDAQ_TREND_DCA, "leveraged", "US"): (US_TQQQ,),
    (NASDAQ_TREND_DCA, "unleveraged", "US"): (US_QQQ,),
    (STRATEGY_9SIG, "growth", "GB"): (_from_instrument(EQUITY_UK),),
    (STRATEGY_9SIG, "safe", "GB"): (UK_ERNS,),
    (ALL_WEATHER, "stocks", "GB"): (VUAG,),
    (ALL_WEATHER, "long", "GB"): (GLTL,),
    (ALL_WEATHER, "inter", "GB"): (IGLT,),
    (ALL_WEATHER, "gold", "GB"): (SGLN,),
    (ALL_WEATHER, "comm", "GB"): (CMOP,),
    (GOLDEN_BUTTERFLY, "stocks", "GB"): (VUAG,),
    (GOLDEN_BUTTERFLY, "scv", "GB"): (USSC,),
    (GOLDEN_BUTTERFLY, "long", "GB"): (GLTL,),
    (GOLDEN_BUTTERFLY, "short", "GB"): (IGLS,),
    (GOLDEN_BUTTERFLY, "gold", "GB"): (SGLN,),
    (DUAL_MOMENTUM, "us", "GB"): (VUAG,),
    (DUAL_MOMENTUM, "intl", "GB"): (XUSE,),
    (DUAL_MOMENTUM, "bonds", "GB"): (VAGS,),
    (ALL_WEATHER, "stocks", "US"): (US_VTI,),
    (ALL_WEATHER, "long", "US"): (US_TLT,),
    (ALL_WEATHER, "inter", "US"): (US_IEF,),
    (ALL_WEATHER, "gold", "US"): (US_GLD,),
    (ALL_WEATHER, "comm", "US"): (US_DBC,),
    (GOLDEN_BUTTERFLY, "stocks", "US"): (US_VTI,),
    (GOLDEN_BUTTERFLY, "scv", "US"): (US_VBR,),
    (GOLDEN_BUTTERFLY, "long", "US"): (US_TLT,),
    (GOLDEN_BUTTERFLY, "short", "US"): (US_SHY,),
    (GOLDEN_BUTTERFLY, "gold", "US"): (US_GLD,),
    (DUAL_MOMENTUM, "us", "US"): (US_SPY,),
    (DUAL_MOMENTUM, "intl", "US"): (US_ACWX,),
    (DUAL_MOMENTUM, "bonds", "US"): (US_AGG,),
    (ALL_WEATHER, "stocks", "DE"): (EU_SXR8,),
    (ALL_WEATHER, "long", "DE"): (EU_DBXF,),
    (ALL_WEATHER, "inter", "DE"): (EU_SXRQ,),
    (ALL_WEATHER, "gold", "DE"): (EU_PPFB,),
    (ALL_WEATHER, "comm", "DE"): (EU_SXRS,),
    (GOLDEN_BUTTERFLY, "stocks", "DE"): (EU_SXR8,),
    (GOLDEN_BUTTERFLY, "scv", "DE"): (EU_ZPRV,),
    (GOLDEN_BUTTERFLY, "long", "DE"): (EU_DBXF,),
    (GOLDEN_BUTTERFLY, "short", "DE"): (EU_DBXP,),
    (GOLDEN_BUTTERFLY, "gold", "DE"): (EU_PPFB,),
    (DUAL_MOMENTUM, "us", "DE"): (EU_SXR8,),
    (DUAL_MOMENTUM, "intl", "DE"): (EU_EXUS,),
    (DUAL_MOMENTUM, "bonds", "DE"): (EU_VAGF,),
    (PERMANENT_PORTFOLIO, "stocks", "GB"): (VUAG,),
    (PERMANENT_PORTFOLIO, "long", "GB"): (GLTL,),
    (PERMANENT_PORTFOLIO, "cashfund", "GB"): (UK_ERNS,),
    (PERMANENT_PORTFOLIO, "gold", "GB"): (SGLN,),
    (PERMANENT_PORTFOLIO, "stocks", "US"): (US_VTI,),
    (PERMANENT_PORTFOLIO, "long", "US"): (US_TLT,),
    (PERMANENT_PORTFOLIO, "cashfund", "US"): (US_BIL,),
    (PERMANENT_PORTFOLIO, "gold", "US"): (US_GLD,),
    (PERMANENT_PORTFOLIO, "stocks", "DE"): (EU_SXR8,),
    (PERMANENT_PORTFOLIO, "long", "DE"): (EU_DBXF,),
    (PERMANENT_PORTFOLIO, "cashfund", "DE"): (EU_XEON,),
    (PERMANENT_PORTFOLIO, "gold", "DE"): (EU_PPFB,),
    (HFEA, "stocks3x", "GB"): (UK_3LUS,),
    (HFEA, "bonds3x", "GB"): (UK_3TYL,),
    (HFEA, "stocks3x", "US"): (US_UPRO,),
    (HFEA, "bonds3x", "US"): (US_TMF,),
    (THREE_SIG, "smallcap", "GB"): (UK_ISP6,),
    (THREE_SIG, "safe", "GB"): (UK_ERNS,),
    (THREE_SIG, "smallcap", "US"): (US_IJR,),
    (THREE_SIG, "safe", "US"): (US_AGG,),
    (THREE_SIG, "smallcap", "DE"): (EU_IUS3,),
    (THREE_SIG, "safe", "DE"): (EU_ERNX,),
    (STRATEGY_9SIG, "growth", "US"): (US_TQQQ,),
    (STRATEGY_9SIG, "safe", "US"): (US_AGG,),
    (STRATEGY_9SIG, "growth", "DE"): (EU_3QQQ,),
    (STRATEGY_9SIG, "safe", "DE"): (EU_ERNX,),
}


def _resolve(strategy_key, role, country, broker, account_type):
    for listing in ROLE_LISTINGS.get((strategy_key, role, country), ()):
        if broker.key in listing.brokers and account_type in listing.wrappers:
            return listing
    return None


def _build():
    rows = {}
    for (strategy_key, role, country), _ in ROLE_LISTINGS.items():
        description = describe_strategy(strategy_key)
        weight = dict(description.role_targets).get(role) if description else None
        if weight is None:
            continue
        for broker in BROKERS.values():
            if broker.country != country:
                continue
            for account_type in broker.account_types:
                listing = _resolve(strategy_key, role, country, broker, account_type)
                if not listing:
                    continue
                rows[(strategy_key, role, country, broker.key, account_type, CATALOGUE_VERSION)] = (
                    CatalogueLine(
                        strategy_key, role, country, broker.key, account_type, CATALOGUE_VERSION,
                        listing.name, listing.ticker, listing.isin, listing.raw_quote_unit,
                        listing.currency, listing.trading_line, weight, "account_check_pending",
                        issuer_url=listing.issuer_url, broker_evidence_url=listing.brokers[broker.key],
                        verified_on=listing.verified_on, note=listing.note, issuer=listing.issuer))
    return rows


_CATALOGUE = _build()
CATALOGUE = MappingProxyType(_CATALOGUE)


def platforms():
    """(key, name, has any strategy) for every known broker, for setup."""
    mapped = {key[3] for key in CATALOGUE}
    return tuple((b.key, b.name, b.key in mapped) for b in BROKERS.values())


# Raw Yahoo quote units for reference series that no strategy holds.
REFERENCE_QUOTE_UNITS = {"TQQQ": ("USD",), "SPY": ("USD",), "QQQ": ("USD",), "GBPUSD=X": ("USD",),
                         # Dual Momentum's sterling cash series (Amundi Smart Overnight Return, pence line).
                         "CSH2.L": ("GBp", "GBX"), "BIL": ("USD",), "XEON.DE": ("EUR",),
                         "EURUSD=X": ("USD",)}


def expected_quote_units():
    """Symbol -> accepted raw Yahoo currency codes, from every catalogued fund.

    A GBX line accepts Yahoo's "GBp"/"GBX"; a GBP line only "GBP". A unit
    change on the feed then holds the price instead of valuing at 100x.
    """
    units = dict(REFERENCE_QUOTE_UNITS)
    for line in CATALOGUE.values():
        if line.ticker and line.raw_quote_unit:
            units[line.ticker] = (("GBp", "GBX") if line.raw_quote_unit == "GBX"
                                  else (line.raw_quote_unit,))
    return units


def fx_symbol(quote_currency, account_currency):
    """Yahoo pair converting a quote currency into the account currency."""
    return "%s%s=X" % (account_currency, quote_currency)


class MappingNotAvailable(ValueError):
    pass


def rows_for(strategy_key, platform, account_type, *, country=None,
             version=CATALOGUE_VERSION):
    if country is None:
        country = BROKERS[platform].country if platform in BROKERS else "GB"
    if version != CATALOGUE_VERSION:
        raise MappingNotAvailable("Mapping version is not released.")
    rows = tuple(
        line for key, line in CATALOGUE.items()
        if key[0] == strategy_key and key[2] == country
        and key[3] == platform and key[4] == account_type
        and key[5] == version
    )
    return rows


def selection_available(strategy_key, platform, account_type):
    offer = OFFERS.get(strategy_key)
    description = describe_strategy(strategy_key)
    rows = rows_for(strategy_key, platform, account_type)
    return bool(
        offer and offer.enabled and description and platform in BROKERS
        # One reviewed line for every role the strategy's rules define.
        and {row.role for row in rows} == {role for role, _ in description.role_targets}
        and all(row.status != "draft" and row.ticker and row.isin for row in rows)
    )


def _manifest_from_lines(strategy_key, platform, account_type):
    """Pinned snapshot for a catalogue-defined strategy (any number of roles)."""
    import hashlib
    import json
    roles = {}
    for line in rows_for(strategy_key, platform, account_type):
        roles[line.role] = {
            "name": line.name, "issuer": line.issuer, "isin": line.isin,
            "broker_ticker": line.ticker.split(".")[0], "yahoo_ticker": line.ticker,
            "trading_line": line.trading_line, "raw_quote_unit": line.raw_quote_unit,
            "normalised_currency": line.currency,
            "weight": str(line.weight),
            "broker_isa_confirmed": False, "owner_product_permission_confirmed": False,
            "issuer_url": line.issuer_url, "broker_evidence_url": line.broker_evidence_url,
        }
        # Pinned snapshots of existing pound accounts must stay byte-identical.
        if line.currency == "GBP":
            roles[line.role]["raw_to_gbp_factor"] = {"GBX": "100", "GBP": "1"}.get(
                line.raw_quote_unit, "GBPUSD=X daily close")
        else:
            roles[line.role]["raw_to_account_factor"] = (
                "1" if line.raw_quote_unit == line.currency
                else "%s daily close" % fx_symbol(line.raw_quote_unit, line.currency))
    manifest = {
        "strategy_key": strategy_key, "mapping_version": CATALOGUE_VERSION,
        "country": BROKERS[platform].country,
        "broker": platform, "account_type": account_type, "roles": roles,
    }
    if BROKERS[platform].currency != "GBP":
        manifest["currency"] = BROKERS[platform].currency
    serial = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest["sha256"] = hashlib.sha256(serial.encode()).hexdigest()
    return manifest


def availability_reason(strategy_key, platform, account_type):
    if selection_available(strategy_key, platform, account_type):
        return ""
    if strategy_key not in OFFERS or not OFFERS[strategy_key].enabled:
        return "This strategy is not released yet."
    if not any(key[0] == strategy_key and key[3] == platform for key in CATALOGUE):
        return "No reviewed funds for this strategy at this broker yet."
    return "No reviewed instruments for this account type. Not available."


def onboarding_manifest(strategy_key, platform, account_type):
    """Return a pinned snapshot; never upgrade a legacy instance implicitly."""
    if not selection_available(strategy_key, platform, account_type):
        raise MappingNotAvailable(availability_reason(strategy_key, platform, account_type))
    if strategy_key != STRATEGY_9SIG or platform != "TRADING212":
        return _manifest_from_lines(strategy_key, platform, account_type)
    # 9Sig keeps its original manifest so existing pinned snapshots still match.
    if account_type == "ISA":
        return enrolment_manifest(
            strategy_key, country="GB", broker="TRADING212",
            account_type="ISA",
        )
    # GIA uses the same exact identified trading lines, not an inferred
    # substitute. Personal dealing permission is still not confirmed.
    import hashlib
    import json
    manifest = enrolment_manifest(
        strategy_key, country="GB", broker="TRADING212",
        account_type="ISA",
    ).copy()
    manifest["account_type"] = "GIA"
    manifest.pop("sha256")
    serial = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    manifest["sha256"] = hashlib.sha256(serial.encode()).hexdigest()
    return manifest


def released_version(strategy_key, currency="GBP"):
    """The rule version a new instance pins, for its account currency."""
    description = describe_strategy(strategy_key)
    if description is None:
        raise MappingNotAvailable("No released strategy rule version.")
    return description.version_for(currency)
