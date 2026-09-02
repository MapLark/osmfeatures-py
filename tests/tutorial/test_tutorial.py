from pathlib import Path
import runpy

TUTORIAL = Path(__file__).parent


def test_places_search():
    runpy.run_path(str(TUTORIAL / "places_search.py"), run_name="__main__")


def test_places_nearby():
    runpy.run_path(str(TUTORIAL / "places_nearby.py"), run_name="__main__")


def test_osm_features_query():
    runpy.run_path(str(TUTORIAL / "osm_features_query.py"), run_name="__main__")
