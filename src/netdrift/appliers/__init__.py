"""Applier package.

To add an in-tree vendor applier: create ``appliers/<vendor>.py`` with a
``@register(...)``-decorated ``apply`` function (see base.py), then add its
module name to ``APPLIER_MODULES`` below. No edits to pipeline.py, cli.py, or
any other core module are needed — registry.py imports these modules so their
@register decorators run.

This list holds module names (strings) only, so ``import netdrift.appliers``
stays cheap and does not pull in napalm/pygnmi; the heavy imports happen when
registry.py loads each module on demand.
"""

APPLIER_MODULES: tuple[str, ...] = ("arista", "cisco")
