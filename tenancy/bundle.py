"""One user's records as a single file, and putting them back. Full fidelity (ids, secrets still
encrypted), so a bundle is as sensitive as a database backup: store it like one."""
import json

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import serializers
from django.db import transaction

VERSION = 1


def _tracker_querysets(user):
    for model in apps.get_app_config("tracker").get_models():
        names = {f.name for f in model._meta.concrete_fields}
        if "owner" in names:
            yield model._default_manager.filter(owner=user)
        elif "strategy" in names:
            yield model._default_manager.filter(strategy__owner=user)
        elif "account" in names:
            yield model._default_manager.filter(account__owner=user)


def collect(user):
    objects = [user]
    from tenancy.models import UserAccess
    objects += list(UserAccess.objects.filter(user=user))
    for queryset in _tracker_querysets(user):
        objects += list(queryset)
    return objects


def export_bundle(user):
    return json.dumps({"wealtharr_bundle": VERSION, "user_id": user.pk,
                       "objects": json.loads(serializers.serialize("json", collect(user)))}, indent=1)


class RestoreError(Exception):
    pass


def restore_bundle(text):
    """Load a bundle into a database that does not have these rows. All or nothing."""
    data = json.loads(text)
    if data.get("wealtharr_bundle") != VERSION:
        raise RestoreError("Not a Wealtharr bundle this version can read.")
    items = list(serializers.deserialize("json", json.dumps(data["objects"])))
    User = get_user_model()
    for item in items:
        obj = item.object
        if type(obj)._default_manager.filter(pk=obj.pk).exists():
            raise RestoreError("%s %s already exists; restore into a database without this user."
                               % (type(obj).__name__, obj.pk))
        if isinstance(obj, User) and User.objects.filter(username=obj.username).exists():
            raise RestoreError("The username %r is taken." % obj.username)
    with transaction.atomic():
        for item in items:
            item.save()
    return len(items)
