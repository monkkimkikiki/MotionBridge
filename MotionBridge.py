"""실행 진입점 (소스 실행 / PyInstaller 공용). 인자 없이 실행하면 GUI, 하위 명령을 주면 CLI."""
import sys

from motionbridge.cli import main

if __name__ == "__main__":
    sys.exit(main())
