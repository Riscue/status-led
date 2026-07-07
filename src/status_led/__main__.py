"""Allow `python3 -m status_led` invocation. Delegates to cli.main()."""
from status_led.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
