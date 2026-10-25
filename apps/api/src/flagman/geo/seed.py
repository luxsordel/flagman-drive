"""Demo zone for the pilot: rentals may end anywhere inside the ring road of Minsk.

The outline is a rough oval over the area where the car emulator parks its cars
(services/car-emulator, MINSK_LAT_RANGE and MINSK_LON_RANGE), so demo rentals can end.
"""

from uuid import UUID

from flagman.geo.domain import ZoneKind

# A fixed id makes the seed safe to run again: the zone is added once, edits made later stay.
DEMO_ZONE_ID = UUID("9d4cba52-b5d3-4e1f-ac78-56e7634215d4")
DEMO_ZONE_NAME = "Minsk inside the ring road"
DEMO_ZONE_KIND = ZoneKind.ALLOWED

# [lon, lat] as in GeoJSON, closed.
_OUTLINE = (
    (27.7000, 53.9050),
    (27.6799, 53.9375),
    (27.6250, 53.9613),
    (27.5500, 53.9700),
    (27.4750, 53.9613),
    (27.4201, 53.9375),
    (27.4000, 53.9050),
    (27.4201, 53.8725),
    (27.4750, 53.8487),
    (27.5500, 53.8400),
    (27.6250, 53.8487),
    (27.6799, 53.8725),
)
DEMO_ZONE_RING = [*_OUTLINE, _OUTLINE[0]]
