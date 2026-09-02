# HitterDNA Park and Weather Contract

Status: PROVISIONAL v0.1, 2026-09-01.

## Rules
- Persist source, query, game/venue IDs, valid/retrieval times, and response hash for every fact.
- Humidity is not modeled.
- Never use raw wind speed as a tailwind. Missing bearing, direction, roof state, provenance, timestamp, or venue identity yields OMITTED.
- No weather probability multiplier or coefficient is defined here.

## Park factors
Use Baseball Savant three-year rolling, event-specific, batter-handedness-specific factors; preserve the 100-is-average index. Required: venue ID, season, rolling years, event, batter side, factor, query, and retrieval time. Sutter Health Park is UNVERIFIED. Rays factors crossing the 2025 outdoor season are rays_continuity_suspect.

## Wind
`wind_from_deg` is meteorological clockwise-from-north. `center_field_bearing_deg` is home-to-center-field clockwise-from-north.

\[ wind\_toward = (wind\_from + 180) \bmod 360 \]
\[ outward\_wind\_mph = wind\_speed\_mph \cdot \cos(wind\_toward - center\_field\_bearing) \]

Positive is outward; negative inward; near zero crosswind. Closed or unknown roofs omit wind.

## Tests
Cover outward, inward, crosswind, missing inputs, closed roof, Sutter Health, Rays metadata, invalid schemas, and absence of humidity.

Sources: Baseball Savant Park Factors and Venue Park Factors; MLB Park Factors Measured by Statcast; Alan Nathan Baseball at High Altitude. Retrieved 2026-09-01.
