import shutil
import sys
from pathlib import Path

def main():
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    shutil.copy2(src, dst)
    print(f"[DUMMY] copied: {src} -> {dst}")

if __name__ == "__main__":
    main()