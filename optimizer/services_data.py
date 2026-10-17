from pathlib import Path

import pandas as pd


# Locate the project directory.
BASE_DIR = Path(__file__).resolve().parent.parent

# Locate the supplied fuel-price CSV.
CSV_PATH = BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv"


# US state and District of Columbia abbreviations.
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE",
    "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}


def load_fuel_stations():
    """Load and clean the fuel-price dataset."""

    if not CSV_PATH.exists():
        raise FileNotFoundError(
            f"Fuel-price CSV not found at: {CSV_PATH}"
        )

    # Read the CSV.
    df = pd.read_csv(CSV_PATH)

    # Remove rows that are completely identical.
    df = df.drop_duplicates()

    # Keep records from supported US states.
    df["State"] = df["State"].astype(str).str.strip().str.upper()
    df = df[df["State"].isin(US_STATES)].copy()

    # Convert fuel prices to numeric values.
    df["Retail Price"] = pd.to_numeric(
        df["Retail Price"],
        errors="coerce"
    )

    # Reject missing, zero, or negative prices.
    df = df[
        df["Retail Price"].notna()
        & (df["Retail Price"] > 0)
    ].copy()

    # Return the cleaned records.
    return df.to_dict(orient="records")
