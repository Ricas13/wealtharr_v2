"""Public, educational guides. Generic terms only; no third-party strategy brands.

Each guide's body lives in templates/public/guides/<slug>.html. Titles stay
under ~60 characters and descriptions under ~155 for search snippets.
"""
from datetime import date

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

GUIDES = [
    {
        "slug": "3x-nasdaq-etf-stocks-and-shares-isa",
        "title": "Holding a 3x Nasdaq ETF in a UK Stocks & Shares ISA",
        "h1": "Can you hold a 3x Nasdaq ETF in a Stocks & Shares ISA?",
        "description": "How UK investors hold a 3x leveraged Nasdaq-100 ETP like LQQ3 inside an ISA: what it is, how daily leverage works, the risks and what to check.",
        "updated": date(2026, 10, 21), "minutes": 6,
        "faqs": [
            ("Can I buy TQQQ in a UK ISA?",
             "Often not. UK platforms usually need a UK-compliant key information document for each fund, and many US funds don't provide one, so UK investors typically use a London-listed, Irish-domiciled product such as LQQ3. Check what your own platform offers."),
            ("Is a 3x ETF the same as owning three times the Nasdaq?",
             "Only for a single day. It resets its leverage daily, so over weeks or months the result can be well above or below three times the index return."),
            ("Do gains in an ISA get taxed?",
             "Gains and income inside a Stocks & Shares ISA are free of UK income tax and capital gains tax. You can pay in up to the annual ISA allowance across all your ISAs."),
        ],
    },
    {
        "slug": "volatility-decay-leveraged-etfs",
        "title": "Volatility Decay in Leveraged ETFs, With Worked Numbers",
        "h1": "Volatility decay in leveraged ETFs, explained with numbers",
        "description": "Why a 3x leveraged ETF can lose money even when its index ends flat. A step-by-step worked example of daily-reset leverage and what it means for you.",
        "updated": date(2026, 10, 21), "minutes": 5,
        "faqs": [
            ("What is volatility decay?",
             "The drag a daily-reset leveraged fund suffers when its index swings up and down. Losses need bigger gains to recover, and leverage magnifies that effect."),
            ("Does volatility decay mean leveraged ETFs always lose?",
             "No. In steady trends the daily compounding can beat three times the index. Decay hurts most in choppy, sideways markets."),
            ("How do people manage it?",
             "Common approaches are holding only part of a portfolio in the leveraged fund and rebalancing on a fixed schedule, so gains are banked and falls are bought with rules rather than emotion."),
        ],
    },
    {
        "slug": "quarterly-signal-rebalancing",
        "title": "Quarterly Signal Rebalancing: A Rules-Based Growth Plan",
        "h1": "Quarterly signal rebalancing: a rules-based way to hold growth",
        "description": "How a quarterly target-growth rebalancing plan works: a growth fund, a safe fund, a fixed growth target each quarter, and simple buy or sell rules.",
        "updated": date(2026, 10, 21), "minutes": 7,
        "faqs": [
            ("How often do you trade?",
             "Once a quarter, shortly after each quarter ends, plus whenever you add money. Many quarters need only one or two trades."),
            ("What happens in a big crash?",
             "Rules-based plans usually keep buying growth from the safe fund as prices fall. Some add a crash rule that pauses selling for a while after a deep market fall so the recovery isn't sold too early."),
            ("Is this financial advice?",
             "No. This guide explains how such plans work. Leveraged funds can lose most of their value, and past results don't predict future ones."),
        ],
    },
    {
        "slug": "gbx-vs-gbp-pence-prices",
        "title": "GBX vs GBP: Why London ETF Prices Look 100x Too Big",
        "h1": "GBX vs GBP: why some London ETF prices look 100 times too big",
        "description": "London-listed ETFs are quoted in pence (GBX) or pounds (GBP). How to tell them apart, and how one unit mix-up can wreck a portfolio spreadsheet.",
        "updated": date(2026, 10, 21), "minutes": 4,
        "faqs": [
            ("What does GBX mean?",
             "Pence sterling. A price of 4,520 GBX is £45.20. GBP prices are already in pounds."),
            ("Why does my spreadsheet show a huge value?",
             "Price feeds such as Yahoo Finance often return pence for London listings. Multiplying shares by a pence price without dividing by 100 makes the holding look 100 times bigger."),
            ("How do I know which unit a fund uses?",
             "Check the listing on the exchange or your broker. The same fund can have different lines in different currencies and units, so match the exact ticker you hold."),
        ],
    },
    {
        "slug": "rebalancing-spreadsheet-vs-app",
        "title": "Tracking a Rebalancing Plan: Spreadsheet or App?",
        "h1": "Tracking a rebalancing plan: spreadsheet or app?",
        "description": "The common ways a rebalancing spreadsheet goes wrong (stale prices, pence errors, missed quarter ends, mid-quarter deposits) and a checklist to avoid them.",
        "updated": date(2026, 10, 21), "minutes": 5,
        "faqs": [
            ("Is a spreadsheet good enough?",
             "It can be, if you keep it disciplined: dated prices, the right currency unit, every deposit recorded, and a reminder at each quarter end."),
            ("Does an app need access to my broker?",
             "Not necessarily. Wealtharr never connects to your broker: you record what you deposit and buy, and it tells you what the plan says to do next."),
            ("What should I record?",
             "Every deposit, withdrawal, buy, sell, dividend and fee with its date, quantity and price. That is enough to rebuild your position and returns at any time."),
        ],
    },
]
BY_SLUG = {g["slug"]: g for g in GUIDES}


@require_GET
def guides_index(request):
    return render(request, "public/guides_index.html", {"guides": GUIDES})


@require_GET
def guide(request, slug):
    item = BY_SLUG.get(slug)
    if item is None:
        raise Http404("No such guide")
    others = [g for g in GUIDES if g["slug"] != slug]
    return render(request, "public/guide.html", {
        "guide": item, "related": others[:3],
        "body_template": "public/guides/%s.html" % slug,
    })
