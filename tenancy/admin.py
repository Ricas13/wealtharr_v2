"""Plans, users' plans and features, user management and the audit log. Owner (superuser) only.

Every change made here is written to the audit log (field names only, never values such as
passwords).
"""
from urllib.parse import urlparse

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from . import audit, features
from .models import AuditEvent, BillingEvent, Plan, UserAccess


def _feature_choices(only_limits=False):
    return [(f.key, "%s (%s)" % (f.label, f.key)) for f in features.all_features() if f.limit == only_limits]


def _https_only(value, label):
    if value and urlparse(value).scheme != "https":
        raise forms.ValidationError("%s must start with https://" % label)
    return value


class PlanForm(forms.ModelForm):
    features = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple, label="Included features",
        help_text="Ignored when 'all features' is ticked.")

    class Meta:
        model = Plan
        fields = ["name", "slug", "description", "price_label", "stripe_link", "stripe_payment_link_id", "paypal_link", "is_public",
                  "sort_order", "all_features", "features", "limits", "is_default"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["features"].choices = _feature_choices()
        if self.instance.pk:
            self.fields["features"].initial = self.instance.features

    def clean_features(self):
        return sorted(self.cleaned_data["features"])

    def clean_stripe_link(self):
        return _https_only(self.cleaned_data["stripe_link"], "The Stripe link")

    def clean_paypal_link(self):
        return _https_only(self.cleaned_data["paypal_link"], "The PayPal link")

    def clean_limits(self):
        limits = self.cleaned_data["limits"] or {}
        if not isinstance(limits, dict):
            raise forms.ValidationError("Limits must be a JSON object such as {\"strategies.max\": 2}.")
        allowed = {f.key for f in features.all_features() if f.limit}
        for key, value in limits.items():
            if key not in allowed:
                raise forms.ValidationError("%r is not a declared numeric limit." % key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise forms.ValidationError("%s must be a whole number, zero or more." % key)
        return limits


class UserAccessForm(forms.ModelForm):
    grant = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
                                      help_text="Extra features for this user on top of their plan.")
    deny = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
                                     help_text="Features withheld from this user even if their plan has them.")

    class Meta:
        model = UserAccess
        fields = ["plan", "grant", "deny", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = _feature_choices()
        for name in ("grant", "deny"):
            self.fields[name].choices = choices
            if self.instance.pk:
                self.fields[name].initial = getattr(self.instance, name)

    def clean(self):
        data = super().clean()
        both = set(data.get("grant", [])) & set(data.get("deny", []))
        if both:
            raise forms.ValidationError("A feature cannot be both granted and denied: %s" % ", ".join(sorted(both)))
        return data

    def clean_grant(self):
        return sorted(self.cleaned_data["grant"])

    def clean_deny(self):
        return sorted(self.cleaned_data["deny"])


def _platform_admin(request):
    return request.user.is_active and request.user.is_superuser


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Add and configure plans: what each includes, its price text and its Stripe/PayPal links."""
    form = PlanForm
    actions = None  # bulk delete would skip the audit line written by delete_model
    list_display = ("name", "slug", "price_label", "is_public", "is_default", "feature_count", "user_count",
                    "has_links")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (None, {"fields": ("name", "slug", "description", "price_label", "is_public", "sort_order", "is_default")}),
        ("Payment links", {"fields": ("stripe_link", "stripe_payment_link_id", "paypal_link"),
                           "description": "Links to a payment page you made on Stripe or PayPal. With a Stripe "
                                          "Payment Link id here and the webhook enabled (STRIPE_WEBHOOK_SECRET), a "
                                          "paid checkout assigns this plan automatically. Otherwise (and always for "
                                          "PayPal) assign the plan yourself under Users."}),
        ("What the plan includes", {"fields": ("all_features", "features", "limits")}),
    )

    def has_module_permission(self, request):
        return _platform_admin(request)

    def has_view_permission(self, request, obj=None):
        return _platform_admin(request)

    has_add_permission = has_change_permission = has_view_permission

    def has_delete_permission(self, request, obj=None):
        return _platform_admin(request) and (obj is None or not obj.users.exists())

    @admin.display(description="Features")
    def feature_count(self, obj):
        return "all" if obj.all_features else len(obj.features)

    @admin.display(description="Users")
    def user_count(self, obj):
        return obj.users.count()

    @admin.display(boolean=True, description="Pay links")
    def has_links(self, obj):
        return bool(obj.stripe_link or obj.paypal_link)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record("plan.changed", actor=request.user, subject=None, request=request, target=obj,
                     metadata={"change": "updated" if change else "created", "fields": sorted(form.changed_data)})

    def delete_model(self, request, obj):
        audit.record("plan.changed", actor=request.user, subject=None, request=request, target=obj,
                     metadata={"change": "deleted"})
        super().delete_model(request, obj)


class UserAccessInline(admin.StackedInline):
    model = UserAccess
    form = UserAccessForm
    can_delete = False
    max_num = 1
    verbose_name = "Plan and features"
    verbose_name_plural = "Plan and features"


def _assign_action(plan):
    def assign(modeladmin, request, queryset):
        for user in queryset:
            UserAccess.objects.update_or_create(user=user, defaults={"plan": plan})
            audit.record("user.plan_assigned", actor=request.user, subject=user, request=request, target=user,
                         metadata={"plan": plan.slug, "via": "bulk action"})
        modeladmin.message_user(request, "%d user(s) moved to %s." % (queryset.count(), plan.name), messages.SUCCESS)
    assign.short_description = "Assign plan: %s" % plan.name
    return assign


class WealtharrUserAdmin(UserAdmin):
    """Edit users by hand, and give them a plan and feature overrides."""
    inlines = [UserAccessInline]
    list_display = ("username", "email", "first_name", "last_name", "is_active", "is_staff", "plan_name")
    list_filter = UserAdmin.list_filter + ("access__plan",)

    @admin.display(description="Plan", ordering="access__plan__name")
    def plan_name(self, obj):
        access = getattr(obj, "access", None)
        return access.plan.name if access else "(default)"

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)  # bulk delete would skip the audit line
        if _platform_admin(request):
            for plan in Plan.objects.all():
                function = _assign_action(plan)
                actions["assign_plan_%d" % plan.pk] = (function, "assign_plan_%d" % plan.pk, function.short_description)
        return actions

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record("user.changed", actor=request.user, subject=obj, request=request, target=obj,
                     metadata={"change": "updated" if change else "created",
                               "fields": sorted(f for f in form.changed_data if "password" not in f)})

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if formset.model is UserAccess and formset.has_changed():
            access = getattr(form.instance, "access", None)
            audit.record("user.plan_assigned", actor=request.user, subject=form.instance, request=request,
                         target=form.instance,
                         metadata={"plan": access.plan.slug if access else "", "via": "user page",
                                   "fields": sorted(k for f in formset.forms for k in f.changed_data)})

    def delete_model(self, request, obj):
        audit.record("user.deleted", actor=request.user, subject=obj, request=request, target=obj, metadata={})
        super().delete_model(request, obj)

    def has_module_permission(self, request):
        return _platform_admin(request)

    def has_view_permission(self, request, obj=None):
        return _platform_admin(request)

    has_add_permission = has_change_permission = has_delete_permission = has_view_permission


User = get_user_model()
admin.site.unregister(User)
admin.site.register(User, WealtharrUserAdmin)


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """A read-only view of the audit log."""
    list_display = ("occurred_at", "action", "actor_label", "subject_id", "target_type", "target_id", "ip")
    list_filter = ("action",)
    search_fields = ("actor_label", "action", "request_id")
    ordering = ("-id",)

    def has_module_permission(self, request):
        return _platform_admin(request)

    def has_view_permission(self, request, obj=None):
        return _platform_admin(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BillingEvent)
class BillingEventAdmin(admin.ModelAdmin):
    """Payment events the webhook has handled. Read-only."""
    list_display = ("received_at", "provider", "event_type", "outcome", "event_id")
    list_filter = ("provider", "event_type")
    ordering = ("-id",)

    def has_module_permission(self, request):
        return _platform_admin(request)

    def has_view_permission(self, request, obj=None):
        return _platform_admin(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
