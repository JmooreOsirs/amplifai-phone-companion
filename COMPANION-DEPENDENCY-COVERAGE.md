# Mac companion minimal revision dependency coverage — local review candidate

**Not a final notices file or redistribution clearance.** This matrix is
mechanically derived from the pinned lock, exact-version PyPI source
archives, the installed wheel metadata, and the frozen helper inventory.
It reports evidence, not a legal license determination. Details and
every source URL/SHA-256 are in `third_party/source-archive-manifest.json`;
helper paths/hashes are in `helper-inventory.json`.

Coverage: **102/102** exact pinned
PyPI source archives present and SHA-256-verified against both PyPI and
the lock. Six sdists have no standalone license/notice file; see notes.
`Observed` means code or distribution files were seen in the frozen
helper. `Not observed` does not prove a dependency is absent from every
transitive binary or copied data file.
This exact helper observes 41 package roots, does not observe
59, and has 2 build tools with runtime-hook evidence.

| Distribution | Version | Frozen helper | License evidence | Sdist notice paths | Audit note |
| --- | --- | --- | --- | ---: | --- |
| `altgraph` | 0.17.5 | not observed in helper | MIT License | 2 |  |
| `annotated-doc` | 0.0.5 | not observed in helper | Expression: MIT | 1 |  |
| `annotated-types` | 0.8.0 | not observed in helper | Expression: MIT | 1 |  |
| `anyio` | 4.15.1 | not observed in helper | Expression: MIT | 1 |  |
| `apple-compress` | 0.2.3 | not observed in helper | No PyPI license field/classifier | 1 |  |
| `arrow` | 1.4.0 | observed in helper | Apache Software License | 1 |  |
| `asn1` | 2.8.0 | not observed in helper | MIT License | 4 |  |
| `asttokens` | 3.0.2 | observed in helper | Metadata text: Apache 2.0 | 1 |  |
| `blessed` | 1.50.0 | not observed in helper | Expression: MIT | 1 |  |
| `bpylist2` | 4.1.1 | observed in helper | MIT License | 0 | MIT text in exact sdist README |
| `certifi` | 2026.7.22 | observed in helper | Mozilla Public License 2.0 (MPL 2.0) | 1 |  |
| `cffi` | 2.1.1 | observed in helper | Expression: MIT-0 | 2 |  |
| `charset-normalizer` | 3.5.1 | observed in helper | Metadata text: MIT | 2 |  |
| `click` | 8.5.0 | observed in helper | Expression: BSD-3-Clause | 2 |  |
| `coloredlogs` | 15.0.1 | not observed in helper | MIT License | 1 |  |
| `construct` | 2.10.70 | observed in helper | MIT License | 1 |  |
| `construct-typing` | 0.8.1 | observed in helper | Expression: MIT | 1 |  |
| `cryptography` | 50.0.1 | observed in helper | Expression: Apache-2.0 OR BSD-3-Clause | 3 |  |
| `daemonize` | 2.5.0 | not observed in helper | MIT License | 1 |  |
| `defusedxml` | 0.7.1 | not observed in helper | Python Software Foundation License | 1 |  |
| `developer-disk-image` | 0.3.0 | not observed in helper | GNU General Public License v3 or later (GPLv3+) | 0 |  |
| `editor` | 1.8.0 | not observed in helper | Expression: MIT | 1 |  |
| `enum-compat` | 0.0.3 | not observed in helper | Metadata text: MIT | 0 |  |
| `executing` | 2.2.1 | observed in helper | MIT License | 1 |  |
| `fastapi` | 0.141.1 | not observed in helper | Expression: MIT | 1 |  |
| `gpxpy` | 1.6.2 | not observed in helper | Metadata text: Apache License, Version 2.0 | 2 |  |
| `h11` | 0.16.0 | not observed in helper | MIT License | 1 |  |
| `hexdump` | 3.3 | observed in helper | Public Domain | 0 | Public Domain declaration; dedication absent |
| `humanfriendly` | 10.0 | not observed in helper | MIT License | 1 |  |
| `hyperframe` | 6.1.0 | observed in helper | MIT License | 1 |  |
| `idna` | 3.20 | observed in helper | Expression: BSD-3-Clause | 1 |  |
| `ifaddr` | 0.2.0 | observed in helper | MIT License | 1 |  |
| `inquirer3` | 0.6.1 | not observed in helper | MIT License | 1 |  |
| `ipsw-parser` | 1.7.5 | not observed in helper | GNU General Public License v3 or later (GPLv3+) | 1 |  |
| `ipython` | 9.17.1 | observed in helper | Expression: BSD-3-Clause | 3 |  |
| `ipython-pygments-lexers` | 1.1.1 | not observed in helper | BSD License | 1 |  |
| `jedi` | 0.20.0 | observed in helper | MIT License | 4 |  |
| `jinxed` | 2.1.0 | not observed in helper | Mozilla Public License 2.0 (MPL 2.0) | 2 |  |
| `loguru` | 0.7.3 | not observed in helper | MIT License | 0 |  |
| `macholib` | 1.16.4 | not observed in helper | MIT License | 2 |  |
| `markdown-it-py` | 4.2.0 | not observed in helper | MIT License | 2 |  |
| `matplotlib-inline` | 0.2.2 | observed in helper | Expression: BSD-3-Clause | 1 |  |
| `mdurl` | 0.1.2 | not observed in helper | MIT License | 1 |  |
| `opack2` | 0.0.1 | not observed in helper | GNU General Public License v3 or later (GPLv3+) | 1 |  |
| `packaging` | 26.3 | observed in helper | Expression: Apache-2.0 OR BSD-2-Clause | 3 |  |
| `parameter-decorators` | 0.0.2 | observed in helper | Long metadata license text (hashed in manifest) | 1 |  |
| `parso` | 0.8.7 | observed in helper | MIT License | 4 |  |
| `pexpect` | 4.9.0 | observed in helper | ISC License (ISCL) | 1 |  |
| `pillow` | 12.3.0 | not observed in helper | Expression: MIT-CMU | 7 |  |
| `plumbum` | 2.0.2 | not observed in helper | Expression: MIT | 1 |  |
| `pmd-net-addr` | 0.0.3 | not observed in helper | Expression: GPL-3.0-or-later | 1 |  |
| `pmd-net-proto` | 0.0.3 | not observed in helper | Expression: GPL-3.0-or-later | 1 |  |
| `pmd-pytcp` | 0.3.7 | not observed in helper | Expression: GPL-3.0-or-later | 1 |  |
| `prompt-toolkit` | 3.0.53 | observed in helper | BSD License | 2 |  |
| `psutil` | 7.2.2 | observed in helper | Metadata text: BSD-3-Clause | 1 |  |
| `ptyprocess` | 0.7.0 | observed in helper | ISC License (ISCL) | 1 |  |
| `pure-eval` | 0.2.4 | observed in helper | MIT License | 1 |  |
| `pycparser` | 3.0 | not observed in helper | Expression: BSD-3-Clause | 1 |  |
| `pycrashreport` | 2.0.0 | not observed in helper | GNU General Public License v3 or later (GPLv3+) | 1 |  |
| `pycryptodome` | 3.23.0 | not observed in helper | BSD License; Public Domain | 7 |  |
| `pydantic` | 2.13.5 | not observed in helper | Expression: MIT | 1 |  |
| `pydantic-core` | 2.46.5 | not observed in helper | Expression: MIT | 1 |  |
| `pygments` | 2.21.0 | observed in helper | Expression: BSD-2-Clause | 3 |  |
| `pygnuutils` | 0.1.1 | observed in helper | GNU General Public License v3 or later (GPLv3+) | 1 |  |
| `pyimg4` | 0.8.8 | not observed in helper | Long metadata license text (hashed in manifest) | 1 |  |
| `pyinstaller` | 6.22.3 | build tool with runtime hook evidence | GNU General Public License v2 (GPLv2) | 4 | boot/runtime hooks included |
| `pyinstaller-hooks-contrib` | 2026.7 | build tool with runtime hook evidence | Apache Software License; GNU General Public License v2 (GPLv2) | 1 | runtime hook evidence; see artifact inventory |
| `pyiosbackup` | 0.2.4 | observed in helper | GNU General Public License v3 or later (GPLv3+) | 1 |  |
| `pykdebugparser` | 1.2.8 | not observed in helper | Long metadata license text (hashed in manifest) | 1 |  |
| `pylzss` | 0.3.4 | not observed in helper | GNU Lesser General Public License v3 (LGPLv3) | 2 |  |
| `pymobiledevice3` | 10.4.0 | observed in helper | Expression: GPL-3.0-or-later | 1 |  |
| `python-dateutil` | 2.9.0.post0 | observed in helper | Apache Software License; BSD License | 2 |  |
| `python-pcapng` | 2.1.1 | not observed in helper | Apache Software License | 1 |  |
| `pytun-pmd3` | 3.0.3 | not observed in helper | GNU General Public License v3 or later (GPLv3+) | 2 |  |
| `pyusb` | 1.3.1 | not observed in helper | BSD License | 1 |  |
| `qh3` | 1.9.4 | not observed in helper | BSD License | 2 |  |
| `readchar` | 4.2.2 | not observed in helper | MIT License | 1 |  |
| `remotezip2` | 0.0.2 | not observed in helper | MIT License | 1 |  |
| `requests` | 2.34.2 | observed in helper | Apache Software License | 2 |  |
| `rich` | 15.0.0 | not observed in helper | MIT License | 1 |  |
| `runs` | 1.3.0 | not observed in helper | Expression: MIT | 0 |  |
| `setuptools` | 84.0.0 | observed in helper | Expression: MIT | 18 |  |
| `shellingham` | 1.5.4 | not observed in helper | ISC License (ISCL) | 1 |  |
| `six` | 1.17.0 | observed in helper | MIT License | 1 |  |
| `srptools` | 1.0.1 | not observed in helper | BSD License | 2 |  |
| `sslpsk-pmd3` | 1.0.3 | not observed in helper | Long metadata license text (hashed in manifest) | 2 |  |
| `stack-data` | 0.6.3 | observed in helper | MIT License | 1 |  |
| `starlette` | 1.7.0 | not observed in helper | Expression: BSD-3-Clause | 1 |  |
| `termcolor` | 3.3.0 | not observed in helper | Expression: MIT | 1 |  |
| `tqdm` | 4.70.1 | observed in helper | Metadata text: MPL-2.0 AND MIT | 1 |  |
| `traitlets` | 5.16.1 | observed in helper | BSD License | 1 |  |
| `typer` | 0.27.2 | not observed in helper | Expression: MIT | 2 |  |
| `typer-injector` | 0.3.0 | not observed in helper | Expression: MIT | 1 |  |
| `typing-extensions` | 4.16.0 | observed in helper | Expression: PSF-2.0 | 1 |  |
| `typing-inspection` | 0.4.4 | not observed in helper | Expression: MIT | 1 |  |
| `tzdata` | 2026.4 | not observed in helper | Metadata text: Apache-2.0 | 2 |  |
| `urllib3` | 2.8.0 | observed in helper | Expression: MIT | 1 |  |
| `uvicorn` | 0.53.0 | not observed in helper | Expression: BSD-3-Clause | 1 |  |
| `wcwidth` | 0.9.0 | observed in helper | MIT License | 2 |  |
| `wsproto` | 1.3.2 | not observed in helper | Expression: MIT | 1 |  |
| `xmod` | 1.10.0 | not observed in helper | Expression: MIT | 1 |  |
| `xonsh` | 0.24.2 | observed in helper | Metadata text: BSD 2-Clause License | 3 |  |

The count of sdist notice paths does not establish that the text is
complete for a particular package, nor does zero mean no applicable
license. See the provisional notices review for the six exact-version
gaps, two metadata conflicts, runtime hooks, and native-library scope.
