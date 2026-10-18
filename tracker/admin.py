from django.contrib import admin
from .models import (Account, LedgerEntry, NotificationDelivery, NotificationPreference,
                     PriceMark, StrategyInstance, StrategyPreset, StrategyBaseline, SystemIntegration, WealthSnapshot)

admin.site.site_header = "Wealtharr | Advanced administration"
admin.site.site_title = "Wealtharr admin"
admin.site.index_title = "Advanced configuration"

@admin.register(StrategyPreset)
class StrategyPresetAdmin(admin.ModelAdmin):
    list_display = ("name", "risk", "active", "slug")
    list_filter = ("risk", "active")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "kind", "currency")
    list_filter = ("kind",)
    readonly_fields = ("currency",)

@admin.register(StrategyInstance)
class StrategyInstanceAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "account", "preset", "monthly_target")
    list_filter = ("preset",)

@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("strategy", "kind", "occurred_on", "symbol", "quantity", "amount")
    list_filter = ("kind",)
    readonly_fields = [f.name for f in LedgerEntry._meta.fields]
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(StrategyBaseline)
class StrategyBaselineAdmin(admin.ModelAdmin):
    list_display = ("strategy", "as_of", "equity_value", "safe_value", "cash_value", "created_at")
    readonly_fields = [f.name for f in StrategyBaseline._meta.fields]
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(PriceMark)
class PriceMarkAdmin(admin.ModelAdmin):
    list_display = ("strategy", "symbol", "price", "as_of")

@admin.register(WealthSnapshot)
class WealthSnapshotAdmin(admin.ModelAdmin):
    list_display = ("owner", "as_of", "total_value", "net_contributed")
    readonly_fields = [f.name for f in WealthSnapshot._meta.fields]
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("owner", "destination_email", "email_enabled", "discord_enabled", "monthly_reminders")

@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = ("owner", "channel", "status", "attempts", "next_retry_at", "created_at")
    list_filter = ("status", "channel")
    actions = ["requeue_failed_deliveries"]

    @admin.action(description="Requeue selected failed notifications")
    def requeue_failed_deliveries(self, request, queryset):
        # Manual recovery after repairing credentials. No direct send in admin.
        count = queryset.filter(status="FAILED").update(
            status="PENDING", attempts=0, next_retry_at=None, error="")
        self.message_user(request, f"{count} delivery(s) queued for the scheduler.")
    readonly_fields = [f.name for f in NotificationDelivery._meta.fields]
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

# Edit credentials through /control/, not Django admin, to keep encrypted fields out of admin forms.

# Production authentication must always pass the Turnstile-gated app login.
# Default Django admin login would otherwise provide an unchallenged sign-in path.
from django.shortcuts import redirect
_original_admin_login = admin.site.login
def _secure_admin_login(request, extra_context=None):
    if not request.user.is_authenticated or not request.user.is_staff:
        return redirect("login")
    return _original_admin_login(request, extra_context=extra_context)
admin.site.login = _secure_admin_login
