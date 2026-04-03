from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    google_places_api_key: str = ""
    rentcast_api_key: str = ""
    db_path: str = "data/hunt.db"
    scrape_delay_min: float = 1.0
    scrape_delay_max: float = 3.0


# Search area bounding box: covers Boca Raton → Lake Worth Beach
SEARCH_BBOX = {
    "north": 26.640,
    "south": 26.340,
    "east": -80.040,
    "west": -80.230,
}

# Default search filters for apartment discovery
DEFAULT_SEARCH = {
    "min_price": 2000,
    "max_price": 3500,
    "min_beds": 2,
}

# Area center coords for convenience
AREA_CENTERS = {
    "delray": (26.4615, -80.0728),
    "boynton": (26.5200, -80.0620),
    "boca": (26.3683, -80.1289),
    "lake_worth": (26.6150, -80.0570),
}

settings = Settings()
