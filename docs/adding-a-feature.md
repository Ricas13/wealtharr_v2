# Adding a feature that plans can switch on or off

A feature is a key the code knows about. Plans and users are configured from the list of keys the
code declares, so nothing can be granted that does not exist. Nothing is gated today: every user is on
the built-in **Full** plan, which includes every feature, including ones added later.

1. **Declare it** where the code that uses it lives (imported at start-up, e.g. in its module):

   ```python
   from tenancy import features
   features.register("study.simulate", "Study what-if", "Move the rule's edges and replay history.")
   features.register("strategies.max", "Number of strategies", limit=True)   # a number, not on/off
   ```

2. **Enforce it** in one place, with the single check:

   ```python
   from tenancy import access
   from tenancy.decorators import require_feature

   @login_required
   @require_feature("study.simulate")          # 403 when the user's plan lacks it
   def study_simulate_json(request, instance_id): ...

   if access.can(request.user, "study.simulate"): ...
   limit = access.limit(request.user, "strategies.max")   # None = unlimited
   ```

   In templates: `{% load access %}{% if user|can:"study.simulate" %}...{% endif %}`.

3. **Configure it** in the admin (platform admin only), under **Admin → Plans**: add a plan (name, price
   text, Stripe and PayPal links, which features it includes, numeric limits as JSON, which plan is the
   default). Under **Admin → Users**, edit any user by hand and set their plan and feature grants or
   denials on the same page, or pick several users and use the "Assign plan: ..." action. Every change is
   written to the audit log.

   The Stripe and PayPal links are plain links to payment pages you made there. The app does not learn
   from them that someone paid, so you assign the plan to the user yourself for now; a Stripe webhook can
   automate that later.

Rules that always hold: a platform admin (superuser) passes every check; an undeclared feature, a
user with no level and no default level, or anything unresolvable is denied; a user's denial beats
their grant, and both beat their plan.

Gate server-side (the decorator or `can()`), not only by hiding a button.
