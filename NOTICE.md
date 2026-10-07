# Attribution, copyright and modification notices

## pt_api

Copyright (c) 2026 Sébastien Bédard.

The current source distribution of pt_api is licensed under the GNU Lesser
General Public License, version 2.1 or (at your option) any later version
(`LGPL-2.1-or-later`). The full text is in LICENSE. COPYING contains the
accompanying GNU General Public License version 2 text referenced by the LGPL;
its inclusion does not change the project's declared license to GPL-only.

## Initial reference: ptformat

ptformat was consulted as a reference and inspiration during initial development,
particularly for understanding the Pro Tools session format and its decryption.
Its foundational format work is acknowledged here:

https://github.com/zamaudio/ptformat

The reference project's source carries these copyright notices:

- Copyright (C) 2015-2019 Damien Zammit
- Copyright (C) 2015-2019 Robin Gareus

ptformat's source states LGPL version 2.1 or any later version. These upstream
rights and notices are preserved; credit does not imply that its authors endorse
pt_api or maintain this Python implementation. Any additional applicable notices
identified during the provenance review must also be retained.

## Modifications and licensing change

pt_api is a Python implementation with its own APIs, session-writing capabilities,
additional format research and regression tests. It is not a verbatim distribution
of the upstream C/C++ library. Its development history is recorded in changelog.md
and in the repository's Git history.

On 2026-10-07, at the maintainer's request, the current source distribution's
MIT-only licensing statement was replaced by LGPL-2.1-or-later, the upstream
reference was credited, and the module header, README and package metadata were
updated. This change does not establish the exact scope of any code derivation.
The detailed provenance review remains ongoing; earlier tags and distributed
releases have not been rewritten and a pending complaint is not automatically
resolved by this change. Existing third-party copyright and license notices must
be preserved.

pt_api is not affiliated with or endorsed by Avid Technology. Pro Tools is an
Avid product.
