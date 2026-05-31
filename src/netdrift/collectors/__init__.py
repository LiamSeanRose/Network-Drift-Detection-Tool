"""Collector package.

To add an in-tree vendor: create ``collectors/<vendor>.py`` with a
``@register(...)``-decorated ``get_reality`` (see base.py), then add its module
name to ``COLLECTOR_MODULES`` below. No edits to pipeline.py, cli.py, or
netbox_client.py are needed — registry.py imports these modules so their
@register decorators run.

This list holds module names (strings) only, so ``import netdrift.collectors``
stays cheap and does not pull in napalm/pygnmi; the heavy imports happen when
registry.py loads each module on demand.
"""

COLLECTOR_MODULES = ("arista", "cisco", "nokia")
