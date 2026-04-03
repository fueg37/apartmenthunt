"""Seed the database with known apartments, gyms, hospitals, and POIs."""
from __future__ import annotations

import asyncio

from db.connection import get_db
from db.locations import get_by_name, insert
from models import Apartment, Gym, Hospital, PointOfInterest


SEED_APARTMENTS: list[Apartment] = [
    Apartment(
        name="Brez at Atlantic Crossing",
        address="656 NE 1st St, Delray Beach, FL 33483",
        lat=26.463247,
        lon=-80.066571,
        rating=4.6,
        review_count=77,
        website_url="https://www.livebrez.com",
        is_top_pick=True,
        notes="Steps from Atlantic Ave, modern building, excellent management (Danny, Cy, Daniela). ~15 min to Bethesda, ~10 min to Delray Medical.",
        apartments_com_slug="brez-at-atlantic-crossing-delray-beach-fl",
    ),
    Apartment(
        name="One Boynton",
        address="1351 S Federal Hwy, Boynton Beach, FL 33435",
        lat=26.515998,
        lon=-80.060433,
        rating=4.6,
        review_count=796,
        website_url="https://www.oneboynton.com",
        is_top_pick=False,
        notes="5 min to Bethesda, best review volume, resort amenities, geographically resilient.",
        apartments_com_slug="one-boynton-boynton-beach-fl",
    ),
    Apartment(
        name="Avion Riverwalk",
        address="630 E Woolbright Rd, Boynton Beach, FL 33435",
        lat=26.513986,
        lon=-80.057313,
        rating=4.6,
        review_count=162,
        website_url="https://avionriverwalk.com",
        is_top_pick=False,
        notes="Intracoastal waterfront, walkable to restaurants, boutique feel. PM Maya Goldstein.",
        apartments_com_slug="avion-riverwalk-boynton-beach-fl",
    ),
    Apartment(
        name="Manatee Bay",
        address="1632 N Federal Hwy, Boynton Beach, FL 33435",
        lat=26.542863,
        lon=-80.055031,
        rating=4.7,
        review_count=254,
        website_url="https://www.manateebayapartments.com",
        is_top_pick=False,
        notes="Private beach, boat marina, closest Boynton apt to Iron Therapy.",
        apartments_com_slug="manatee-bay-boynton-beach-fl",
    ),
    Apartment(
        name="South of Atlantic",
        address="151 SE 3rd Ave, Delray Beach, FL 33483",
        lat=26.45892,
        lon=-80.069867,
        rating=4.7,
        review_count=263,
        website_url="https://www.southofatlantic.com",
        is_top_pick=False,
        notes="Just south of Atlantic Ave, good Brez alternative.",
        apartments_com_slug="south-of-atlantic-delray-beach-fl",
    ),
    Apartment(
        name="Windsor at Delray Beach",
        address="2001 N Federal Hwy, Delray Beach, FL 33483",
        lat=26.484626,
        lon=-80.064104,
        rating=4.7,
        review_count=180,
        website_url="https://www.windsorcommunities.com/properties/windsor-at-delray-beach/",
        is_top_pick=False,
        notes="High-end finishes, north Delray. Thin wall complaints noted.",
        apartments_com_slug="windsor-at-delray-beach-delray-beach-fl",
    ),
]

SEED_GYMS: list[Gym] = [
    Gym(
        name="Iron Therapy Gym",
        address="1100 Barnett Dr #44, Lake Worth Beach, FL 33461",
        lat=26.62971,
        lon=-80.068978,
        rating=4.8,
        review_count=73,
        website_url="https://www.irontherapygym.com",
        is_top_pick=True,
        notes="Powerlifting/strongman, 24/7, owner Jon builds community. Closest serious gym (~10 min from Boynton).",
        equipment_highlights=["Powerlifting platforms", "Strongman equipment", "24/7 access"],
    ),
    Gym(
        name="Elev8tion Fitness",
        address="141 NW 20th St, Boca Raton, FL 33431",
        lat=26.368719,
        lon=-80.087045,
        rating=4.5,
        review_count=128,
        website_url="https://elev8tionfitness.com",
        is_top_pick=True,
        notes="200lb DBs, serious athlete culture, GM Julio. ~25 min from Boynton.",
        equipment_highlights=["200lb dumbbells", "Athlete culture", "Heavy equipment"],
    ),
    Gym(
        name="RedCon1 Gym",
        address="990 S Rogers Cir STE 7, Boca Raton, FL 33487",
        lat=26.402563,
        lon=-80.106142,
        rating=4.1,
        review_count=245,
        website_url="https://redcon1gym.com",
        is_top_pick=False,
        notes="Brand gym, bodybuilding culture, $25 day pass. ~20 min from Boynton.",
        equipment_highlights=["Bodybuilding culture", "Day passes available"],
    ),
]

SEED_HOSPITALS: list[Hospital] = [
    Hospital(
        name="Baptist Bethesda Hospital East",
        address="2815 S Seacrest Blvd, Boynton Beach, FL 33435",
        lat=26.504293,
        lon=-80.070442,
        rating=3.4,
        review_count=1037,
        is_top_pick=False,
        notes="Baptist Health system, most central to family network. 5 min from One Boynton/Avion.",
        health_system="Baptist Health",
    ),
    Hospital(
        name="Delray Medical Center",
        address="5352 Linton Blvd, Delray Beach, FL 33484",
        lat=26.436778,
        lon=-80.127132,
        rating=4.6,
        review_count=6630,
        is_top_pick=True,
        notes="Highest rated hospital, 'Dream Team' staff. ~10 min from Brez.",
        health_system="Tenet Health",
    ),
    Hospital(
        name="Baptist Boca Raton Regional",
        address="800 Meadows Rd, Boca Raton, FL 33486",
        lat=26.359016,
        lon=-80.102869,
        rating=4.3,
        review_count=3920,
        is_top_pick=False,
        notes="Baptist flagship. ~20 min from both Brez and One Boynton.",
        health_system="Baptist Health",
    ),
]


SEED_POIS: list[PointOfInterest] = [
    # ── Grocery: Whole Foods (weight 1.0) ──
    PointOfInterest(
        name="Whole Foods Market Boca Raton",
        address="1400 Glades Rd, Boca Raton, FL 33431",
        lat=26.3690, lon=-80.1290,
        category="grocery", weight=1.0,
        website_url="https://www.wholefoodsmarket.com",
    ),
    PointOfInterest(
        name="Whole Foods Market Delray Beach",
        address="1400 S Federal Hwy, Delray Beach, FL 33483",
        lat=26.4475, lon=-80.0700,
        category="grocery", weight=1.0,
        website_url="https://www.wholefoodsmarket.com",
    ),
    # ── Grocery: Publix (weight 0.8) ──
    PointOfInterest(
        name="Publix Boynton Beach Federal Hwy",
        address="1620 S Federal Hwy, Boynton Beach, FL 33435",
        lat=26.5150, lon=-80.0610,
        category="grocery", weight=0.8,
        website_url="https://www.publix.com",
    ),
    PointOfInterest(
        name="Publix Delray Beach Atlantic Ave",
        address="1095 E Atlantic Ave, Delray Beach, FL 33483",
        lat=26.4590, lon=-80.0620,
        category="grocery", weight=0.8,
        website_url="https://www.publix.com",
    ),
    PointOfInterest(
        name="Publix Boca Raton Glades Rd",
        address="8903 Glades Rd, Boca Raton, FL 33434",
        lat=26.3820, lon=-80.1780,
        category="grocery", weight=0.8,
        website_url="https://www.publix.com",
    ),
    # ── Grocery: Trader Joe's (weight 0.4) ──
    PointOfInterest(
        name="Trader Joe's Boca Raton",
        address="21065 Powerline Rd, Boca Raton, FL 33433",
        lat=26.3595, lon=-80.1445,
        category="grocery", weight=0.4,
        website_url="https://www.traderjoes.com",
    ),
    PointOfInterest(
        name="Trader Joe's Delray Beach",
        address="1800 S Federal Hwy, Delray Beach, FL 33483",
        lat=26.4445, lon=-80.0705,
        category="grocery", weight=0.4,
        website_url="https://www.traderjoes.com",
    ),
    # ── Grocery: Aldi (weight 0.3) ──
    PointOfInterest(
        name="Aldi Boynton Beach",
        address="3452 S Federal Hwy, Boynton Beach, FL 33435",
        lat=26.4920, lon=-80.0655,
        category="grocery", weight=0.3,
        website_url="https://www.aldi.us",
    ),
    PointOfInterest(
        name="Aldi Delray Beach",
        address="4789 W Atlantic Ave, Delray Beach, FL 33445",
        lat=26.4580, lon=-80.1150,
        category="grocery", weight=0.3,
        website_url="https://www.aldi.us",
    ),
    # ── Beach access (weight 0.9) ──
    PointOfInterest(
        name="Delray Beach Public Beach",
        address="Atlantic Ave at A1A, Delray Beach, FL 33483",
        lat=26.4590, lon=-80.0620,
        category="beach", weight=0.9,
    ),
    PointOfInterest(
        name="Oceanfront Park Boynton Beach",
        address="6415 N Ocean Blvd, Ocean Ridge, FL 33435",
        lat=26.5175, lon=-80.0570,
        category="beach", weight=0.9,
    ),
    PointOfInterest(
        name="Red Reef Park Boca Raton",
        address="1400 N Ocean Blvd, Boca Raton, FL 33432",
        lat=26.3880, lon=-80.0600,
        category="beach", weight=0.9,
    ),
    PointOfInterest(
        name="South Beach Park Boca Raton",
        address="400 N Rycroft Rd, Boca Raton, FL 33432",
        lat=26.3570, lon=-80.0600,
        category="beach", weight=0.9,
    ),
    # ── Parks & trails (weight 0.7) ──
    PointOfInterest(
        name="Wakodahatchee Wetlands",
        address="13026 Jog Rd, Delray Beach, FL 33446",
        lat=26.4350, lon=-80.1020,
        category="park", weight=0.7,
    ),
    PointOfInterest(
        name="Green Cay Wetlands",
        address="6220 Boy Scout Rd, Boynton Beach, FL 33437",
        lat=26.5210, lon=-80.1060,
        category="park", weight=0.7,
    ),
    PointOfInterest(
        name="Spanish River Park Boca Raton",
        address="3001 N Ocean Blvd, Boca Raton, FL 33431",
        lat=26.4010, lon=-80.0650,
        category="park", weight=0.7,
    ),
    # ── Pharmacy (weight 0.6) ──
    PointOfInterest(
        name="CVS Pharmacy Boynton Beach Federal Hwy",
        address="1801 S Federal Hwy, Boynton Beach, FL 33435",
        lat=26.5160, lon=-80.0610,
        category="pharmacy", weight=0.6,
        website_url="https://www.cvs.com",
    ),
    PointOfInterest(
        name="Walgreens Delray Beach Atlantic Ave",
        address="2285 W Atlantic Ave, Delray Beach, FL 33445",
        lat=26.4580, lon=-80.0870,
        category="pharmacy", weight=0.6,
        website_url="https://www.walgreens.com",
    ),
    PointOfInterest(
        name="CVS Pharmacy Boca Raton Glades Rd",
        address="1300 Glades Rd, Boca Raton, FL 33431",
        lat=26.3680, lon=-80.1290,
        category="pharmacy", weight=0.6,
        website_url="https://www.cvs.com",
    ),
    # ── General retail: Target (weight 0.6) / Costco (weight 0.5) ──
    PointOfInterest(
        name="Target Boynton Beach",
        address="801 N Congress Ave, Boynton Beach, FL 33426",
        lat=26.5290, lon=-80.0920,
        category="general_retail", weight=0.6,
        website_url="https://www.target.com",
    ),
    PointOfInterest(
        name="Target Boca Raton",
        address="1400 Glades Rd, Boca Raton, FL 33431",
        lat=26.3670, lon=-80.1290,
        category="general_retail", weight=0.6,
        website_url="https://www.target.com",
    ),
    PointOfInterest(
        name="Target Delray Beach",
        address="14897 Military Trail, Delray Beach, FL 33484",
        lat=26.4530, lon=-80.1050,
        category="general_retail", weight=0.6,
        website_url="https://www.target.com",
    ),
    PointOfInterest(
        name="Costco Boca Raton",
        address="20801 State Rd 7, Boca Raton, FL 33498",
        lat=26.3650, lon=-80.1700,
        category="general_retail", weight=0.5,
        website_url="https://www.costco.com",
    ),
]


async def seed_db(db_path: str | None = None) -> int:
    """Insert seed data. Returns the number of records inserted/updated."""
    count = 0
    async with get_db(db_path) as db:
        all_locations = [*SEED_APARTMENTS, *SEED_GYMS, *SEED_HOSPITALS, *SEED_POIS]
        for loc in all_locations:
            await insert(db, loc)
            count += 1
    return count


if __name__ == "__main__":
    n = asyncio.run(seed_db())
    print(f"Seeded {n} locations.")
