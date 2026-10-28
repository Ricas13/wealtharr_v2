"""Users and Plans as native app pages (platform admin only), with the same audit trail as the Django admin.

The Django admin stays available for everything else and every page here links to its admin twin,
so nothing is hidden: this is the everyday view, the admin is the full one.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm
from django.contrib.sessions.models import Session
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from . import audit, features, gateways
from .admin import PlanForm, UserAccessForm
from .models import AuditEvent, Plan, SupportGrant, UserAccess
from .support import platform_admin

User = get_user_model()
PAGE_SIZE = 25
PROVIDER_FIELDS = {"stripe": "stripe_price_id", "paypal": "paypal_plan_id"}


def _shell(request, **extra):
    from tracker.v2_views import _instances
    return {"shell_instances": _instances(request), **extra}


def _others_can_admin(exclude_pk):
    return User.objects.filter(is_superuser=True, is_active=True).exclude(pk=exclude_pk).exists()


# ---- users ---------------------------------------------------------------------------------

class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "is_active", "is_staff", "is_superuser"]
        labels = {"is_active": "Active (can sign in)", "is_staff": "Staff (can open this area)",
                  "is_superuser": "Platform admin (full access)"}

    def __init__(self, *args, acting=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.acting = acting

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another account already uses that email.")
        return email

    def clean(self):
        data = super().clean()
        user = self.instance
        if self.acting is not None and user.pk == self.acting.pk:
            for name in ("is_active", "is_staff", "is_superuser"):
                if getattr(user, name) and not data.get(name):
                    self.add_error(name, "You can't take this away from your own account.")
        if user.is_superuser and not (data.get("is_superuser") and data.get("is_active")):
            if not _others_can_admin(user.pk):
                raise forms.ValidationError("There must be at least one active platform admin.")
        return data


class NewUserForm(UserCreationForm):
    email = forms.EmailField(required=False)
    plan = forms.ModelChoiceField(Plan.objects.all(), required=False, empty_label="(the default plan)")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "first_name", "last_name")


def _filtered_users(request):
    rows = User.objects.select_related("access__plan").order_by("username")
    q = (request.GET.get("q") or "").strip()
    if q:
        rows = rows.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q)
                           | Q(last_name__icontains=q))
    plan = request.GET.get("plan") or ""
    if plan == "default":
        rows = rows.filter(access__isnull=True)
    elif plan.isdigit():
        rows = rows.filter(access__plan_id=int(plan))
    status = request.GET.get("status") or ""
    if status == "active":
        rows = rows.filter(is_active=True)
    elif status == "inactive":
        rows = rows.filter(is_active=False)
    role = request.GET.get("role") or ""
    if role == "staff":
        rows = rows.filter(is_staff=True)
    elif role == "admin":
        rows = rows.filter(is_superuser=True)
    return rows, {"q": q, "plan": plan, "status": status, "role": role}


@platform_admin
def users_list(request):
    rows, filters = _filtered_users(request)
    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    keep = "&".join(f"{k}={v}" for k, v in filters.items() if v)
    return render(request, "tenancy/console_users.html", _shell(
        request, page_key="console_users", page_title="Users", page=page, filters=filters, total=page.paginator.count,
        plans=Plan.objects.all(), keep=keep, default_plan=Plan.objects.filter(is_default=True).first()))


@platform_admin
@require_POST
def users_bulk(request):
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    users = list(User.objects.filter(pk__in=ids))
    action = request.POST.get("action", "")
    done = 0
    if action.startswith("plan:"):
        plan = Plan.objects.filter(pk=action[5:]).first()
        for user in (users if plan else []):
            UserAccess.objects.update_or_create(user=user, defaults={"plan": plan})
            audit.record("user.plan_assigned", actor=request.user, subject=user, request=request, target=user,
                         metadata={"plan": plan.slug, "via": "users page bulk"})
            done += 1
        messages.success(request, "%d user(s) moved to %s." % (done, plan.name)) if plan else messages.error(request, "Choose a plan.")
    elif action in ("activate", "deactivate"):
        want = action == "activate"
        for user in users:
            if not want and (user.pk == request.user.pk or (user.is_superuser and not _others_can_admin(user.pk))):
                messages.warning(request, "%s was left active (you can't deactivate yourself or the last platform admin)." % user.get_username())
                continue
            if user.is_active != want:
                user.is_active = want
                user.save(update_fields=["is_active"])
                audit.record("user.changed", actor=request.user, subject=user, request=request, target=user,
                             metadata={"change": "updated", "fields": ["is_active"], "via": "users page bulk"})
                done += 1
        messages.success(request, "%d user(s) %s." % (done, "activated" if want else "deactivated"))
    else:
        messages.error(request, "Choose an action first.")
    return redirect(request.POST.get("next") or "console_users")


@platform_admin
@require_http_methods(["GET", "POST"])
def user_new(request):
    form = NewUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.email = form.cleaned_data["email"]
        user.save()
        plan = form.cleaned_data["plan"]
        if plan:
            UserAccess.objects.update_or_create(user=user, defaults={"plan": plan})
        audit.record("user.changed", actor=request.user, subject=user, request=request, target=user,
                     metadata={"change": "created", "fields": sorted(set(form.changed_data) - {"password1", "password2"}),
                               "via": "users page"})
        messages.success(request, "User %s created." % user.get_username())
        return redirect("console_user", user.pk)
    return render(request, "tenancy/console_user_new.html", _shell(
        request, page_key="console_users", page_title="Add user", form=form))


def _end_sessions(user):
    ended = 0
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        if str(session.get_decoded().get("_auth_user_id")) == str(user.pk):
            session.delete()
            ended += 1
    return ended


@platform_admin
@require_http_methods(["GET", "POST"])
def user_detail(request, pk):
    subject = get_object_or_404(User.objects.select_related("access__plan"), pk=pk)
    access = getattr(subject, "access", None)
    kind = request.POST.get("form_kind", "") if request.method == "POST" else ""
    profile = UserForm(request.POST if kind == "profile" else None, instance=subject, acting=request.user, prefix="profile")
    access_form = UserAccessForm(request.POST if kind == "access" else None,
                                 instance=access or UserAccess(user=subject), prefix="access")
    password = SetPasswordForm(subject, request.POST if kind == "password" else None, prefix="password")
    ok = None
    if kind == "profile" and profile.is_valid():
        profile.save()
        audit.record("user.changed", actor=request.user, subject=subject, request=request, target=subject,
                     metadata={"change": "updated", "fields": sorted(profile.changed_data), "via": "user page"})
        ok = "Profile saved."
    elif kind == "access" and access_form.is_valid():
        row = access_form.save(commit=False)
        row.user = subject
        row.save()
        audit.record("user.plan_assigned", actor=request.user, subject=subject, request=request, target=subject,
                     metadata={"plan": row.plan.slug, "via": "user page", "fields": sorted(access_form.changed_data)})
        ok = "Plan and features saved."
    elif kind == "password" and password.is_valid():
        password.save()
        audit.record("user.password_set", actor=request.user, subject=subject, request=request, target=subject, metadata={})
        ok = "Password changed. Their other sessions were signed out."
        _end_sessions(subject)
    elif kind == "reset_email":
        from .accounts import ResetForm
        reset = ResetForm({"email": subject.email}) if subject.email and subject.is_active else None
        if reset is not None and reset.is_valid():
            reset.save(request=request, use_https=request.is_secure(),
                       email_template_name="tenancy/reset_email.txt", subject_template_name="tenancy/reset_subject.txt")
            audit.record("user.reset_email_sent", actor=request.user, subject=subject, request=request, target=subject, metadata={})
            ok = "A password-reset email is on its way to %s." % subject.email
        else:
            messages.error(request, "That account has no email address or is not active.")
    elif kind == "signout":
        count = _end_sessions(subject)
        audit.record("user.sessions_ended", actor=request.user, subject=subject, request=request, target=subject,
                     metadata={"count": count})
        ok = "Signed out %d session(s)." % count
    if ok:
        messages.success(request, ok)
        return redirect("console_user", subject.pk)
    from tracker.models import ProviderSubscription
    return render(request, "tenancy/console_user.html", _shell(
        request, page_key="console_users", page_title=subject.get_username(), subject=subject, access=access,
        profile=profile, access_form=access_form, password=password,
        plan=access.plan if access else Plan.objects.filter(is_default=True).first(),
        subscriptions=ProviderSubscription.objects.filter(user=subject).order_by("-created_at"),
        events=AuditEvent.objects.filter(subject_id=subject.pk).order_by("-id")[:15],
        support_open=SupportGrant.objects.filter(user=subject, revoked_at__isnull=True, expires_at__gt=timezone.now()).exists(),
        admin_url=reverse("admin:auth_user_change", args=[subject.pk])))


# ---- plans ---------------------------------------------------------------------------------

class ConsolePlanForm(PlanForm):
    """The plan form with friendly inputs: a number per limit, and the price/plan id at each gateway."""
    stripe_price_id = forms.CharField(required=False, label="Stripe price id (price_…, recurring)")
    paypal_plan_id = forms.CharField(required=False, label="PayPal plan id (P-…)")

    class Meta(PlanForm.Meta):
        fields = [f for f in PlanForm.Meta.fields if f != "limits"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.limits if self.instance.pk else {}
        for feature in features.all_features():
            if feature.limit:
                self.fields["limit__" + feature.key] = forms.IntegerField(
                    required=False, min_value=0, label=feature.label, help_text="Leave blank for no limit. " + feature.description,
                    initial=current.get(feature.key))
        if self.instance.pk:
            for mapping in self.instance.provider_mappings.all():
                self.fields[PROVIDER_FIELDS[mapping.provider]].initial = mapping.external_id
        for name, field in self.fields.items():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                field.widget.attrs.setdefault("class", "w2-input")

    def limit_fields(self):
        return [self[name] for name in self.fields if name.startswith("limit__")]

    def clean(self):
        data = super().clean()
        for provider, name in PROVIDER_FIELDS.items():
            value = (data.get(name) or "").strip()
            if value and not gateways.EXTERNAL_ID.match(value):
                self.add_error(name, "That doesn't look like a valid id.")
            data[name] = value
        return data

    def save(self, commit=True):
        plan = super().save(commit=False)
        plan.limits = {name[7:]: value for name, value in self.cleaned_data.items()
                       if name.startswith("limit__") and value is not None}
        if commit:
            plan.save()
        return plan

    def save_mappings(self, plan):
        """Store the gateway ids and ask each gateway about them. Returns [(label, ok, note)]."""
        from tracker.models import PlanProviderMapping
        results = []
        for provider, name in PROVIDER_FIELDS.items():
            value = self.cleaned_data.get(name, "")
            existing = PlanProviderMapping.objects.filter(plan=plan, provider=provider).first()
            if not value:
                if existing:
                    existing.delete()
                continue
            if existing is None or existing.external_id != value:
                existing, _ = PlanProviderMapping.objects.update_or_create(
                    plan=plan, provider=provider, defaults={"external_id": value, "verified_at": None, "verification_note": ""})
            ok = gateways.verify_mapping(existing)
            results.append((gateways.LABELS[provider], ok, existing.verification_note))
        return results


@platform_admin
def plans_list(request):
    plans = list(Plan.objects.annotate(user_count=Count("users")).order_by("sort_order", "name"))
    from tracker.models import PlanProviderMapping
    verified = {(m.plan_id, m.provider): m.verified_at is not None for m in PlanProviderMapping.objects.all()}
    for plan in plans:
        plan.stripe_state = verified.get((plan.pk, "stripe"))
        plan.paypal_state = verified.get((plan.pk, "paypal"))
        plan.feature_summary = "all" if plan.all_features else len(plan.features)
    default_users = User.objects.filter(access__isnull=True).count()
    return render(request, "tenancy/console_plans.html", _shell(
        request, page_key="console_plans", page_title="Plans", plans=plans, default_users=default_users,
        admin_url=reverse("admin:tenancy_plan_changelist")))


@platform_admin
@require_http_methods(["GET", "POST"])
def plan_edit(request, pk=None):
    plan = get_object_or_404(Plan, pk=pk) if pk else None
    form = ConsolePlanForm(request.POST or None, instance=plan)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        results = form.save_mappings(saved)
        audit.record("plan.changed", actor=request.user, subject=None, request=request, target=saved,
                     metadata={"change": "updated" if plan else "created", "fields": sorted(form.changed_data),
                               "via": "plans page"})
        messages.success(request, "Plan saved.")
        for label, ok, note in results:
            (messages.success if ok else messages.warning)(request, "%s: %s" % (label, "verified" if ok else "not verified (%s)" % note))
        return redirect("console_plan", saved.pk)
    from tracker.models import PlanProviderMapping
    mappings = {m.provider: m for m in PlanProviderMapping.objects.filter(plan=plan)} if plan else {}
    return render(request, "tenancy/console_plan.html", _shell(
        request, page_key="console_plans", page_title=plan.name if plan else "New plan", plan=plan, form=form,
        mappings=mappings, ready={p: gateways.checkout_ready(p) for p in gateways.PROVIDERS},
        can_delete=bool(plan and not plan.is_default and not plan.users.exists()),
        admin_url=reverse("admin:tenancy_plan_change", args=[plan.pk]) if plan else reverse("admin:tenancy_plan_add")))


@platform_admin
@require_POST
def plan_delete(request, pk):
    plan = get_object_or_404(Plan, pk=pk)
    if plan.is_default or plan.users.exists():
        messages.error(request, "A plan that is the default or has users can't be deleted. Move its users first.")
        return redirect("console_plan", plan.pk)
    audit.record("plan.changed", actor=request.user, subject=None, request=request, target=plan,
                 metadata={"change": "deleted", "via": "plans page"})
    plan.delete()
    messages.success(request, "Plan deleted.")
    return redirect("console_plans")
