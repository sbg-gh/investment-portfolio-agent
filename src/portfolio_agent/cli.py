"""Offline smoke demonstration, with a fresh in-memory database each run."""
import argparse
import json
from .core import FixtureValidator, Store, ValidatedSecurity

def demo():
    store = Store()
    try:
        store.create_user("demo-user")
        portfolio = store.create_portfolio("demo-user", "Example portfolio")
        validator = FixtureValidator({"DEMO": ValidatedSecurity("fixture:demo", "DEMO", "etf", "US", "synthetic-fixture")})
        store.import_holding("demo-user", portfolio, "DEMO", "2.5", validator)
        store.add_private_memory("demo-user", "episodic", "Fixture import succeeded", "demo-run")
        print(json.dumps({"mode": "read_only", "holdings": store.holdings("demo-user", portfolio),
                          "memory_count": len(store.retrieve_memory("demo-user"))}, indent=2))
    finally:
        store.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo"])
    parser.parse_args()
    demo()

if __name__ == "__main__":
    main()
