# Third-party notices

trade-monitor is MIT licensed (see [LICENSE](../LICENSE)). It builds on
open-source software, all under permissive licenses. This file records them so
redistribution obligations are met and so users can audit exactly what they run.

The MIT license of trade-monitor itself does not cover these dependencies; each
remains under its own license and copyright.

## Runtime dependencies

| Package | License | Copyright | Used for |
|---|---|---|---|
| yfinance | Apache-2.0 | Ran Aroussi and contributors | Yahoo Finance data feed |
| requests | Apache-2.0 | Kenneth Reitz and contributors | universe list fetch |
| beautifulsoup4 | MIT | Leonard Richardson | NSE page parsing (planned) |
| exchange_calendars | Apache-2.0 | Quantopian and contributors | trading calendars |
| matplotlib | PSF License | Matplotlib Development Team | chart rendering (planned) |

## Transitive dependencies

| Package | License | Notes |
|---|---|---|
| pandas | BSD-3-Clause | via yfinance |
| numpy | BSD-3-Clause, 0BSD, MIT, Zlib, CC0 | via pandas |
| python-dateutil | BSD-3-Clause / Apache-2.0 | via pandas |
| pytz | MIT | via pandas |
| lxml / html5lib | BSD / MIT | via beautifulsoup4 |
| uPlot | MIT | dashboard charts (planned, vendored) |

## Development dependencies (never shipped)

| Package | License |
|---|---|
| pytest | MIT |

## Policy

Only OSI-approved, permissive licenses are allowed as dependencies: **MIT,
Apache-2.0, BSD, ISC, PSF, Zlib, 0BSD, CC0.** Copyleft licenses (GPL, AGPL,
LGPL) and source-available licenses (SSPL, BUSL) are not permitted, so that
trade-monitor stays freely redistributable and embeddable by anyone.

The engine core (`trade_monitor/`) imports only the Python standard library.
Third-party libraries appear only at the edges (datafeeds, rendering).

See [spec/DEPENDENCIES.md](../spec/DEPENDENCIES.md) for the enforced policy and
the command that checks it.

## Apache-2.0 notice

Dependencies under Apache-2.0 are used under the terms of that license. A copy
of the Apache License, Version 2.0 may be obtained at
<https://www.apache.org/licenses/LICENSE-2.0>. Where required, modifications to
Apache-2.0 files are noted; trade-monitor does not modify its Apache-2.0
dependencies.
