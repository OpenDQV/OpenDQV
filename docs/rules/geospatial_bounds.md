# `geospatial_bounds` Rule

Validates that a latitude/longitude coordinate pair falls within a geographic bounding box.

## How It Works

The rule treats the `field` being validated as the **latitude** value.  A companion
field (`geo_lon_field`) supplies the **longitude** value from the same record.  Both
values are checked against the bounding box defined by the four optional parameters
(`geo_min_lat`, `geo_max_lat`, `geo_min_lon`, `geo_max_lon`).

Basic coordinate validity is always enforced:

- Latitude must be in **[-90, 90]**
- Longitude must be in **[-180, 180]**

If `geo_lon_field` is omitted, only the latitude field is validated (useful for
hemisphere checks or coarse zone filtering where longitude is not relevant).

## Parameters

| Parameter        | Type    | Required | Description                                        |
|------------------|---------|----------|----------------------------------------------------|
| `geo_lon_field`  | string  | No       | Field name containing the longitude value          |
| `geo_min_lat`    | float   | No       | Minimum latitude bound (inclusive, -90 to 90)      |
| `geo_max_lat`    | float   | No       | Maximum latitude bound (inclusive, -90 to 90)      |
| `geo_min_lon`    | float   | No       | Minimum longitude bound (inclusive, -180 to 180)   |
| `geo_max_lon`    | float   | No       | Maximum longitude bound (inclusive, -180 to 180)   |

All five parameters are optional — omit any bound to leave that dimension unconstrained.

## Example YAML

### UK bounding box (logistics, ride-hailing)

```yaml
- name: delivery_within_uk
  type: geospatial_bounds
  field: pickup_lat
  geo_lon_field: pickup_lon
  geo_min_lat: 49.0
  geo_max_lat: 61.0
  geo_min_lon: -8.5
  geo_max_lon: 2.0
  error_message: "Pickup coordinates must be within the UK"
  severity: error
```

### European Union bounding box (GDPR data residency)

```yaml
- name: location_within_eu
  type: geospatial_bounds
  field: lat
  geo_lon_field: lon
  geo_min_lat: 34.5
  geo_max_lat: 71.2
  geo_min_lon: -25.0
  geo_max_lon: 45.0
  error_message: "Record location must be within the EU"
  severity: error
```

### Continental United States bounding box (real estate, insurance)

```yaml
- name: property_within_conus
  type: geospatial_bounds
  field: property_lat
  geo_lon_field: property_lon
  geo_min_lat: 24.5
  geo_max_lat: 49.5
  geo_min_lon: -125.0
  geo_max_lon: -66.9
  error_message: "Property must be located within the contiguous United States"
  severity: error
```

### Northern hemisphere only (no longitude constraint)

```yaml
- name: northern_hemisphere
  type: geospatial_bounds
  field: lat
  geo_min_lat: 0.0
  geo_max_lat: 90.0
  error_message: "Latitude must be in the northern hemisphere"
  severity: warning
```

## Use Cases

**Logistics depot validation** — Ensure inbound shipment origin/destination coordinates
fall within contracted service areas.  Reject records with coordinates outside the
operating region before they reach the warehouse management system.

**Supply chain geography** — Validate that supplier locations declared in procurement
records actually correspond to the stated country or region.  Catches copy-paste errors
and fraudulent origin claims.

**Real estate data quality** — Verify that property listing coordinates are plausible
for the stated address country.  Out-of-range coordinates indicate geocoding failures
that would corrupt map-based analytics.

**Ride-hailing geofencing** — Confirm that pickup and drop-off coordinates are within
the licensed operating zone before dispatching a driver.  Records outside the bounding
box are flagged for manual review rather than silently accepted.

**GDPR / data residency** — Detect records that claim an EU location but have
coordinates outside EU member state boundaries, which may indicate data provenance
issues relevant to residency compliance.

## Batch Mode

The rule is fully supported in `validate_batch()` (DuckDB-powered batch validation).
Each row is checked independently; invalid latitude or longitude type conversions
(non-numeric values) are treated as failures.
