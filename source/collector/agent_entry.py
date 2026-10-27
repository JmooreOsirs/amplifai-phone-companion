"""PyInstaller entrypoint for the local-only portable helper candidate."""

import sys


def main():
    from amplifai_phone.helper_protocol import configure_protocol_streams

    configure_protocol_streams(sys.stdin, sys.stdout)
    if sys.argv[1:2] == ['install-verified-update']:
        if sys.platform != 'win32':
            return 2
        from importlib import import_module

        return import_module('update_installer').main(sys.argv[2:])
    if sys.argv[1:2] == ['companion-status']:
        from amplifai_phone.companion_status import main as status_main

        return status_main(sys.argv[2:])
    # Handle the bounded, session-confined worker before importing device SDKs.
    if sys.argv[1:] == ['--internal-contacts-size-validation']:
        from amplifai_phone.sqlite_size_compat import worker_main

        return worker_main()
    if sys.argv[1:] == ['--internal-messages-size-validation']:
        from amplifai_phone.sqlite_messages_size_compat import worker_main

        return worker_main()
    if sys.argv[1:] == ['--internal-encrypted-size-validation']:
        from amplifai_phone.sqlite_encrypted_size_compat import worker_main

        return worker_main()
    from amplifai_phone.agent import main as agent_main

    return agent_main()

if __name__ == "__main__":
    raise SystemExit(main())
