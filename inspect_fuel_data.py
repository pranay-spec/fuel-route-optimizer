from pathlib import Path
import pandas as pd

# Find the CSV file relative to this script.
BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv"

# Load the CSV into a pandas DataFrame.
df = pd.read_csv(CSV_PATH)

# Display basic information about the dataset.
print("First five records:")
print(df.head())

print("\nColumn names:")
print(df.columns.tolist())

print("\nDataset dimensions:")
print(df.shape)

print("\nMissing values per column:")
print(df.isnull().sum())

print("\nDuplicate rows:")
print(df.duplicated().sum())
