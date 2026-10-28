"""The features a plan can switch on or off. Declared once, enforced where each is used.

The built-in "Full" plan includes all of these, so declaring a feature changes nothing for
anyone until the owner builds a narrower plan in Admin -> Plans.
"""
from tenancy import features

features.register("strategies.max", "Number of strategies", "How many strategies a user can set up.", limit=True)
features.register("alerts.email", "Email alerts", "Alerts delivered by email.")
features.register("alerts.discord", "Discord alerts", "Alerts as a Discord DM or channel post.")
features.register("alerts.telegram", "Telegram alerts", "Alerts as Telegram messages.")
features.register("study.simulate", "Study what-if", "Move a rule's edges and replay stored history.")
