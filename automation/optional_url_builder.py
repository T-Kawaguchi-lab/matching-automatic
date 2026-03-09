from pathlib import Path
import sys

def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dummy_url_latest.xlsx")
    out.write_bytes(b"")
    print(f"[DUMMY] created: {out}")

if __name__ == "__main__":
    main()