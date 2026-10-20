# trade-monitor-contracts

The strategy plugin interface for [trade-monitor](https://github.com/pothak/trade-monitor).

**Zero dependencies.** A strategy author installs this — not the engine — so a
plugin never inherits the engine's dependency tree or version constraints.

```bash
pip install trade-monitor-contracts
```

```python
from trade_monitor_contracts import (
    Bar, PriceSeries, Fundamentals, Evidence, Thesis, Invalidation,
    DataFeed, PatternDetector, Strategy, Validator, DeliveryAdapter,
)

class MyStrategy:
    name = "my_strategy"
    needs_fundamentals = False

    def score(self, series, fundamentals, patterns=None, **options):
        ...  # return a grade, a (grade, score) tuple, or an object with .grade/.score

    def thesis(self, *, series, fundamentals, patterns=None,
               evidence, grade, confidence, **options):
        ...  # return a Thesis
```

Drop the file in a scanned strategy directory and trade-monitor loads it at
runtime. No engine import, no rebuild.

MIT licensed. See [DEPENDENCIES](https://github.com/pothak/trade-monitor/blob/main/spec/DEPENDENCIES.md).
