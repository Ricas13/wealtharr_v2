"""The features a plan can include, declared in code.

Adding a feature is one declaration here (or anywhere that is imported at start-up) plus one
``can()`` check where it is enforced. Plans are configured in the admin from this list, so
nothing can be granted that the code does not know about.
"""
import re
from dataclasses import dataclass

KEY = re.compile(r"^[a-z][a-z0-9_.]{1,59}$")


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    description: str = ""
    limit: bool = False  # a number (e.g. strategies.max) rather than on/off


_registry = {}


def register(key, label, description="", *, limit=False):
    """Declare a feature. Re-declaring the same key with the same meaning is harmless."""
    if not KEY.match(key):
        raise ValueError("Feature keys are lower-case words and dots, like 'study.simulate': %r" % key)
    feature = Feature(key, label, description, limit)
    existing = _registry.get(key)
    if existing is not None and existing != feature:
        raise ValueError("Feature %r is already declared differently" % key)
    _registry[key] = feature
    return feature


def get(key):
    return _registry.get(key)


def all_features():
    return sorted(_registry.values(), key=lambda feature: feature.key)
