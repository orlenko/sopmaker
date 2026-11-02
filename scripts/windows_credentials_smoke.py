"""Opt-in Windows-only test with an isolated temporary credential-store service."""

import argparse
import sys
import uuid

import sopmaker.credentials as credentials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-credential-store", action="store_true", required=True)
    parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("This check requires Windows.")
    credentials.SERVICE = "org.sopmaker.tests." + uuid.uuid4().hex
    key = "invented-windows-test-credential"
    first = credentials.Credentials(False)
    try:
        first.set(key, remember=True)
        second = credentials.Credentials(False)
        assert second.load_saved() and second.key == key
        second.forget()
        assert not credentials.Credentials(False).load_saved()
        print("Isolated Windows Credential Manager save/load/delete checks passed.")
    finally:
        first.forget()


if __name__ == "__main__":
    main()
