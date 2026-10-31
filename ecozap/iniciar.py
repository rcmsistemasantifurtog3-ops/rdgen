"""Ponto de entrada usado pelo PyInstaller para gerar EcoZap.exe e ecozap-cli.exe."""
import sys

from ecozap.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
