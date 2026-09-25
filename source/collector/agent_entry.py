"""PyInstaller entrypoint for the local-only portable helper candidate."""

from amplifai_phone.agent import main

if __name__ == "__main__":
    raise SystemExit(main())
