"""PyInstaller entrypoint for the local-only portable helper candidate."""

import sys


def main():
    # Handle the bounded, session-confined worker before importing device SDKs.
    if sys.argv[1:] == ['--internal-contacts-size-validation']:
        from amplifai_phone.sqlite_size_compat import worker_main

        return worker_main()
    if sys.argv[1:] == ['--internal-messages-size-validation']:
        from amplifai_phone.sqlite_messages_size_compat import worker_main

        return worker_main()
    from amplifai_phone.agent import main as agent_main

    return agent_main()

if __name__ == "__main__":
    raise SystemExit(main())
