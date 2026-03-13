"""Tests for geospatial_bounds rule."""
from core.rule_parser import Rule
from core.validator import validate_record, validate_batch


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
