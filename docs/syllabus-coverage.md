# Syllabus coverage

Each row is code that does a job in PulseGuard. Line numbers are the definition or the statement that uses the idea.

## OOP

| Topic | Where |
| --- | --- |
| `BaseCheck` ABC and `CheckMeta` | `src/pulseguard/checks/base.py` lines 26 and 58 |
| `Notifier` ABC | `src/pulseguard/notifiers/base.py` line 10 |
| `Repository` ABC | `src/pulseguard/storage/base.py` line 15 |
| Constructors | `BaseCheck.__init__` at `checks/base.py` line 73; `Engine.__init__` at `core/engine.py` line 67 |
| `__del__` only on the agent client | `src/pulseguard/agent/client.py` lines 30 and 94. Context managers are the normal close path. |
| Class variable `kind` | `checks/base.py` line 69, `checks/http_check.py` line 62 |
| Instance state `_target`, name-mangled `__interval`, `@property` | `checks/base.py` lines 66 and 111–124 |
| Single inheritance | `HttpCheck` at `checks/http_check.py` line 51. MRO test: `tests/test_inheritance_mro.py` line 38 |
| Multilevel | `KeywordCheck(HttpCheck)` at `checks/keyword_check.py` line 22. MRO test line 39 |
| Hierarchical | `TcpCheck` line 29, `DnsCheck` line 30, `HeartbeatCheck` line 32. MRO test lines 40–42 |
| Multiple | `TlsExpiryCheck(SerializableMixin, BaseCheck)` at `checks/tls_check.py` line 58. MRO test lines 43–49 |
| Hybrid | `DefacementCheck(KeywordCheck, AlertThresholdMixin)` at `checks/defacement.py` line 25. MRO test lines 50–62 |
| Polymorphism | `check.run()` via `ScheduledCheck` in `core/scheduler.py` line 13; `Notifier.send` in `notifiers/base.py`; `SqliteRepository` and `MysqlRepository` both subclass `SqlRepository` |
| Operators | `Severity.__lt__` `checks/models.py` line 56; `LatencyStats.__add__` line 172; `CheckResult.__eq__` line 117; `IncidentLog.__len__` / `__iter__` / `__contains__` from line 215 |

## Functions

| Topic | Where |
| --- | --- |
| Closure with `nonlocal` | `core/decorators.py` lines 38 and 59, retry attempts |
| Recursion and cycle detection | `core/dependencies.py` `flatten_monitors` line 19, `root_cause` line 57, `_walk` line 101 |
| Lambda, `map`, `filter`, `reduce` | `core/sla.py` lines 29–37 |
| Custom iterator | `IncidentCursor` and `IncidentLog` in `checks/models.py` lines 194 and 215 |
| Generator `yield` | `storage/base.py` line 50 and `storage/sql_repo.py` line 154, paged history |
| Decorators | `@retry`, `@timed`, `@log_call` in `core/decorators.py` lines 30, 86, and 105. `@require_api_key` in `web/auth.py` line 121 |
| Context managers | `Timer` and `HostLimiter` in `core/context.py` lines 20 and 38; `SqlRepository.transaction` in `storage/sql_repo.py` line 249; `AgentClient` and `Collector` |
| Metaclass registry | `CheckMeta` in `checks/base.py` line 26. Missing `kind` fails class creation at line 45 |
| Duck-typed notifiers | `ConsoleNotifier` and `EmailNotifier` both implement `send`. There is no `importlib` plugin loader |

## Files, exceptions, storage

| Topic | Where |
| --- | --- |
| Rotating logs | `logging_setup.py` line 62 |
| Binary agent frames | `agent/protocol.py` line 29, `struct.Struct("!4sBBI")` |
| Binary backup and SHA-256 | `exporters/backup.py` line 18, header `!4sIQ32s` |
| CSV import and export | `exporters/csv_export.py` lines 22, 45, and 64 |
| JSON config, API, status | `core/engine.py` `load_plan` line 399; `web/app.py` status and monitor routes |
| Exception hierarchy | `exceptions.py` lines 10–51 |
| Assertions | `checks/models.py` lines 107–115; `core/incidents.py` line 89; `agent/protocol.py` line 30 |
| SQLite CRUD | `storage/sqlite_repo.py` line 19 and `storage/sql_repo.py` |
| MySQL backend | `storage/mysql_repo.py` line 90 |

## Threads, network, APIs

| Topic | Where |
| --- | --- |
| Thread pool and sliding window | `core/engine.py` lines 13, 256, and 273 |
| `Lock`, `Event` | `core/engine.py` lines 116–118 |
| `BoundedSemaphore` | `core/context.py` line 67; `agent/server.py` line 57 |
| Processes for CPU scoring | `ml/anomaly.py` `detect_in_process` line 34 and `time_modes` line 50. The benchmark script prints the measured seconds |
| TCP check and agent sockets | `checks/tcp_check.py` line 29; `agent/client.py` line 27; `agent/server.py` line 36 |
| JSON API and `/ping/<token>` | `web/app.py` `create_app` line 47 |
| `requests` | `checks/http_check.py` line 155 |
| `smtplib` | `notifiers/email_notifier.py` line 89. `send_digest` is explicit. Nothing calls it on a timer |
| BeautifulSoup | `checks/keyword_check.py` line 151, parser `html.parser` |

## GUI and web

| Topic | Where |
| --- | --- |
| `ttk.Treeview`, `pack`, `grid`, `after` | `gui/widgets.py` lines 34, 54, and 58 |
| Main-thread widget rule | `gui/widgets.py` `TkStatusView` line 24 |
| Queue from the loader | `gui/console.py` line 29 |
| SLA calculator | `gui/sla_calculator.py` `format_budget` line 33, using `error_budget_remaining` |
| Flask routes, Jinja, forms | `web/app.py`, `web/templates/`, `web/forms.py` line 9 |
| Session login and CSRF | `web/auth.py` lines 71, 103, and 136 |
| Container | `Dockerfile` runs as `pulseguard`. `docker-compose.yml` publishes `127.0.0.1:8080` |
