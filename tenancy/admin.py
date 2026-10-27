"""Configure access levels and users' access; read the audit log. Owner (superuser) only."""
from django import forms
from django.contrib import admin

from . import audit, features
from .models import AccessLevel, AuditEvent, UserAccess


def _feature_choices(only_limits=False):
    return [(f.key, "%s (%s)" % (f.label, f.key)) for f in features.all_features() if f.limit == only_limits]


class AccessLevelForm(forms.ModelForm):
    features = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple, label="Included features",
        help_text="Ignored when 'all features' is ticked.")

    class Meta:
        model = AccessLevel
        fields = ["name", "slug", "all_features", "features", "limits", "is_default"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["features"].choices = _feature_choices()
        if self.instance.pk:
            self.fields["features"].initial = self.instance.features

    def clean_features(self):
        return sorted(self.cleaned_data["features"])

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
                                      help_text="Extra features for this user on top of their level.")
    deny = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
                                     help_text="Features withheld from this user even if their level has them.")

    class Meta:
        model = UserAccess
        fields = ["user", "level", "grant", "deny", "note"]

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


class AuditedAdmin(admin.ModelAdmin):
    """Every change is written to the audit log; platform admins only."""
    audit_action = ""

    actions = None  # bulk delete would skip the audit line written by delete_model

    def _allowed(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_module_permission(self, request):
        return self._allowed(request)

    has_view_permission = has_add_permission = has_change_permission = has_delete_permission = _allowed

    def _subject(self, obj):
        return None

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record(self.audit_action, actor=request.user, subject=self._subject(obj), request=request,
                     target=obj, metadata={"change": "updated" if change else "created",
                                           "fields": sorted(form.changed_data)})

    def delete_model(self, request, obj):
        audit.record(self.audit_action, actor=request.user, subject=self._subject(obj), request=request,
                     target=obj, metadata={"change": "deleted"})
        super().delete_model(request, obj)


@admin.register(AccessLevel)
class AccessLevelAdmin(AuditedAdmin):
    audit_action = "access.level_changed"
    form = AccessLevelForm
    list_display = ("name", "slug", "all_features", "is_default", "feature_count")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="Features")
    def feature_count(self, obj):
        return "all" if obj.all_features else len(obj.features)


@admin.register(UserAccess)
class UserAccessAdmin(AuditedAdmin):
    audit_action = "access.user_changed"
    form = UserAccessForm
    list_display = ("user", "level", "note")
    autocomplete_fields = ("user",)

    def _subject(self, obj):
        return obj.user


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """A read-only view of the audit log."""
    list_display = ("occurred_at", "action", "actor_label", "subject_id", "target_type", "target_id", "ip")
    list_filter = ("action",)
    search_fields = ("actor_label", "action", "request_id")
    ordering = ("-id",)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
