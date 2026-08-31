import os
from osmfeatures import OSMFeaturesClient

client = OSMFeaturesClient(
    api_key=os.environ["MAPLARK_API_KEY"],
    base_url=os.environ.get("MAPLARK_BASE_URL", "https://api.maplark.com"),
)
cafes = client.places_search(
    location={"lat": 59.316, "lng": 18.075},
    radius=800,
    or_tags=["amenity=cafe"],
    open_now=True,
)
print(cafes)
