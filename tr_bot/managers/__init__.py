"""Operational managers: fund, portfolio, advisory, training.

Submodules are imported lazily (not re-exported eagerly here) to avoid a
circular import: `tr_bot.managers.fund` imports from `tr_bot.trade_ledger`,
which in turn imports `tr_bot.managers.security`. Eagerly importing
`tr_bot.managers.fund` from this package's `__init__` -- which Python always
runs before any submodule import -- would make `import tr_bot.trade_ledger`
fail whenever it happens to be the first `tr_bot.managers*` import in a
process. Import the specific submodule instead, e.g.
`from tr_bot.managers.fund import FundManager`.
"""
