"""Human labels for codes in the product data. Stored with derived = true where they are our reading of a code."""

# GS1 allergen type codes seen in the data (allergen / nonAllergen classifications)
ALLERGENS = {
    "AC": "Crustaceans", "AE": "Eggs", "AF": "Fish", "AM": "Milk", "AN": "Tree nuts", "AP": "Peanuts",
    "AS": "Sesame", "AU": "Sulphites", "AW": "Cereals containing gluten", "AY": "Soybeans", "BC": "Celery",
    "BM": "Mustard", "NL": "Lupin", "UM": "Molluscs", "UW": "Wheat", "NR": "Rye", "GB": "Barley", "GO": "Oats",
    "SA": "Almonds", "SH": "Hazelnuts", "ST": "Walnuts", "SC": "Cashews", "SP": "Pecans", "SR": "Brazil nuts",
    "SM": "Macadamia nuts", "SW": "Pistachios", "GK": "Kamut", "GS": "Spelt",
}

# Sales unit abbreviations (Finnish ERP style)
SALES_UNITS = {
    "ST": "piece", "KI": "carton", "KG": "kilogram", "PAK": "pack", "BAG": "bag", "RAS": "box (rasia)",
    "PRK": "tub (purkki)", "BOT": "bottle", "SK": "sack (säkki)", "CS": "case", "KLE": "package (kolli)",
    "KRT": "carton (kartonki)", "KAN": "canister", "PAL": "pallet", "NIP": "bundle (nippu)",
}

# nutritionalClaim codes with a cleaner label than the generated one
CLAIM_WORDS = {
    "FREE_FROM_LACTOSE": "Lactose free", "LOW_LACTOSE": "Low lactose", "FREE_FROM_GLUTEN": "Gluten free",
    "VEGAN": "Vegan", "VEGETARIAN": "Vegetarian", "FREE_FROM_EGGS": "Egg free",
    "NO_ADDED_SUGARS": "No added sugars", "HIGH_PROTEIN": "High protein", "HIGH_FIBRE": "High fibre",
}
