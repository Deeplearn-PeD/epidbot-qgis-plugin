"""Tests for the pure helpers of :mod:`core.layer_loader`."""

import os

from core.layer_loader import (GeoLoadError, _unique_layer_name,
                               sniff_csv_coordinate_columns)


def _write_csv(tmp_path, header, rows):
    path = os.path.join(str(tmp_path), "data.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        for row in rows:
            fh.write(row + "\n")
    return path


class TestSniffCsvCoordinateColumns:
    """Lat/lon column detection for CSV dataset URLs."""

    def test_latitude_longitude(self, tmp_path):
        path = _write_csv(
            tmp_path, "municipio,latitude,longitude,casos",
            ["Rio de Janeiro,-22.9,-43.2,10",
             "Niteroi,-22.88,-43.1,5"])
        assert sniff_csv_coordinate_columns(path) == ("latitude", "longitude")

    def test_lat_lon(self, tmp_path):
        path = _write_csv(tmp_path, "city,lat,lon",
                          ["A,-1.0,2.0", "B,-3.0,4.0"])
        assert sniff_csv_coordinate_columns(path) == ("lat", "lon")

    def test_sinan_style_columns(self, tmp_path):
        path = _write_csv(tmp_path, "NU_ANO,NU_LATITUDE,NU_LONGITUDE",
                          ["2024,-22.9,-43.2", "2024,-23.5,-46.6"])
        assert sniff_csv_coordinate_columns(path) == ("NU_LATITUDE",
                                                     "NU_LONGITUDE")

    def test_no_coordinates(self, tmp_path):
        path = _write_csv(tmp_path, "municipio,casos",
                          ["Rio de Janeiro,10"])
        assert sniff_csv_coordinate_columns(path) == (None, None)

    def test_non_numeric_coordinate_values_are_rejected(self, tmp_path):
        path = _write_csv(tmp_path, "municipio,lat,lon",
                          ["Rio de Janeiro,not_a_number,-43.2"])
        assert sniff_csv_coordinate_columns(path) == (None, None)

    def test_empty_file(self, tmp_path):
        path = os.path.join(str(tmp_path), "empty.csv")
        open(path, "w").close()
        assert sniff_csv_coordinate_columns(path) == (None, None)

    def test_missing_file(self, tmp_path):
        assert sniff_csv_coordinate_columns(
            os.path.join(str(tmp_path), "nope.csv")) == (None, None)


class TestUniqueLayerName:
    """Layer-name sanitization."""

    def test_truncates_long_names(self):
        assert len(_unique_layer_name("x" * 500)) == 80

    def test_fallback_for_empty_name(self):
        assert _unique_layer_name("") == "EpidBot layer"


class TestGeoLoadError:
    def test_is_an_exception(self):
        try:
            raise GeoLoadError("boom")
        except GeoLoadError as exc:
            assert str(exc) == "boom"
