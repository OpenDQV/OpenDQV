"""Tests for geospatial_bounds rule."""
from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_record, validate_batch


class TestGeospatialBounds:

    def _uk_rule(self):
        return Rule(
            name="r", type="geospatial_bounds", field="lat",
            geo_lon_field="lon",
            geo_min_lat=49.0, geo_max_lat=61.0,
            geo_min_lon=-8.5, geo_max_lon=2.0,
            error_message="Coordinates outside UK bounding box"
        )

    def test_london_coords_pass(self):
        result = validate_record({"lat": 51.5, "lon": -0.12}, [self._uk_rule()])
        assert result["valid"] is True

    def test_edinburgh_coords_pass(self):
        result = validate_record({"lat": 55.95, "lon": -3.2}, [self._uk_rule()])
        assert result["valid"] is True

    def test_paris_fails(self):
        # Paris is south of UK bounding box
        result = validate_record({"lat": 48.85, "lon": 2.35}, [self._uk_rule()])
        assert result["valid"] is False

    def test_new_york_fails(self):
        result = validate_record({"lat": 40.71, "lon": -74.0}, [self._uk_rule()])
        assert result["valid"] is False

    def test_invalid_lat_fails(self):
        rule = Rule(name="r", type="geospatial_bounds", field="lat",
                    error_message="Invalid coordinates")
        result = validate_record({"lat": 999.0}, [rule])
        assert result["valid"] is False

    def test_none_lat_fails(self):
        result = validate_record({"lat": None, "lon": 0.0}, [self._uk_rule()])
        assert result["valid"] is False

    def test_lat_only_bounds(self):
        rule = Rule(name="r", type="geospatial_bounds", field="lat",
                    geo_min_lat=0.0, geo_max_lat=90.0,
                    error_message="Must be northern hemisphere")
        assert validate_record({"lat": 45.0}, [rule])["valid"] is True
        assert validate_record({"lat": -10.0}, [rule])["valid"] is False

    def test_batch_mode(self):
        rule = self._uk_rule()
        records = [
            {"lat": 51.5, "lon": -0.12},   # London — valid
            {"lat": 48.85, "lon": 2.35},   # Paris — invalid
            {"lat": 53.48, "lon": -2.24},  # Manchester — valid
        ]
        result = validate_batch(records, [rule])
        assert result["summary"]["passed"] == 2
        assert result["results"][1]["valid"] is False

    # ── Boundary values ────────────────────────────────────────────────────────

    def test_exact_boundary_lat_min_passes(self):
        """Exactly at geo_min_lat=49.0 should be within bounds (inclusive)."""
        result = validate_record({"lat": 49.0, "lon": 0.0}, [self._uk_rule()])
        assert result["valid"] is True

    def test_exact_boundary_lat_max_passes(self):
        """Exactly at geo_max_lat=61.0 should be within bounds (inclusive)."""
        result = validate_record({"lat": 61.0, "lon": 0.0}, [self._uk_rule()])
        assert result["valid"] is True

    def test_just_outside_lat_max_fails(self):
        result = validate_record({"lat": 61.01, "lon": 0.0}, [self._uk_rule()])
        assert result["valid"] is False

    def test_exact_boundary_lon_min_passes(self):
        result = validate_record({"lat": 55.0, "lon": -8.5}, [self._uk_rule()])
        assert result["valid"] is True

    def test_exact_boundary_lon_max_passes(self):
        result = validate_record({"lat": 55.0, "lon": 2.0}, [self._uk_rule()])
        assert result["valid"] is True

    # ── Invalid / edge-case values ─────────────────────────────────────────────

    def test_string_lat_fails(self):
        """Non-numeric string in lat field should fail."""
        result = validate_record({"lat": "not_a_number", "lon": 0.0}, [self._uk_rule()])
        assert result["valid"] is False

    def test_none_lon_fails(self):
        """None in the lon field should fail when a lon rule is configured."""
        result = validate_record({"lat": 51.5, "lon": None}, [self._uk_rule()])
        assert result["valid"] is False

    def test_missing_lon_field_fails(self):
        """Missing lon key entirely should fail."""
        result = validate_record({"lat": 51.5}, [self._uk_rule()])
        assert result["valid"] is False

    def test_absolute_lat_out_of_range_fails(self):
        """lat=91 is outside the physically possible range, should fail."""
        rule = Rule(
            name="r", type="geospatial_bounds", field="lat",
            geo_min_lat=-90.0, geo_max_lat=90.0,
            error_message="lat must be valid"
        )
        result = validate_record({"lat": 91.0}, [rule])
        assert result["valid"] is False

    def test_absolute_lon_out_of_range_fails(self):
        """lon=181 is outside the physically possible range, should fail."""
        rule = Rule(
            name="r", type="geospatial_bounds", field="lat",
            geo_lon_field="lon",
            geo_min_lon=-180.0, geo_max_lon=180.0,
            error_message="lon must be valid"
        )
        result = validate_record({"lat": 0.0, "lon": 181.0}, [rule])
        assert result["valid"] is False
