"""One-shot script: ingest IndicePA data, generate mock scans, export open data."""
from src.ingest import ingest_pa
from src.mock_data import generate_mock_scans
from src.exporter import export_all

if __name__ == "__main__":
    print("=== Step 1: Ingest IndicePA ===")
    ingest_pa()
    print()
    print("=== Step 2: Generate mock scan data ===")
    generate_mock_scans()
    print()
    print("=== Step 3: Export open data ===")
    export_all()
    print()
    print("Done! Run 'python run.py' to start the web server.")
