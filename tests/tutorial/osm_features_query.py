import os
from osmfeatures import OSMFeaturesClient

client = OSMFeaturesClient(
    api_key=os.environ["MAPLARK_API_KEY"],
    base_url=os.environ.get("MAPLARK_BASE_URL", "https://api.maplark.com"),
)
buildings = client.query(
    bbox="18.06,59.32,18.09,59.34",
    tags=["building"],
)
print(len(buildings["features"]), "buildings found")
