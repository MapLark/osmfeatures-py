import os
from osmfeatures import OSMFeaturesClient

client = OSMFeaturesClient(
    api_key=os.environ["MAPLARK_API_KEY"],
    base_url=os.environ.get("MAPLARK_BASE_URL", "https://api.maplark.com"),
)
nearby = client.places_nearby(
    location={"lat": 59.316, "lng": 18.075},
    or_tags=["amenity=pharmacy"],
    radius=1000,
    limit=5,
)
print(nearby)
