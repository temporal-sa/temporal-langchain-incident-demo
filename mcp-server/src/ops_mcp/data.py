"""Canned operational data per service. Each service's data points the agent toward
one remediation, which is what makes each demo scenario repeatable."""

SERVICES = {
    "checkout-api": {
        "owner": "team-checkout", "tier": 1, "current_version": "3.8.2",
        "runbook": "https://runbooks.example.com/checkout-api",
        "dependencies": ["cart-cache", "payments-api", "inventory-api"],
    },
    "payments-api": {
        "owner": "team-payments", "tier": 0, "current_version": "2.14.0",
        "runbook": "https://runbooks.example.com/payments-api",
        "dependencies": ["ledger-db", "fraud-scoring"],
    },
    "inventory-api": {
        "owner": "team-fulfilment", "tier": 1, "current_version": "5.2.0",
        "runbook": "https://runbooks.example.com/inventory-api",
        "dependencies": ["inventory-db", "warehouse-sync"],
    },
    "search-api": {
        "owner": "team-discovery", "tier": 2, "current_version": "1.22.3",
        "runbook": "https://runbooks.example.com/search-api",
        "dependencies": ["search-index"],
    },
    "orders-api": {
        "owner": "team-orders", "tier": 1, "current_version": "4.1.0",
        "runbook": "https://runbooks.example.com/orders-api",
        "dependencies": ["orders-db", "payments-api"],
    },
    "legacy-billing": {
        "owner": "finance-platform", "tier": 2, "current_version": "0.9.7",
        "runbook": "https://runbooks.example.com/legacy-billing",
        "dependencies": ["billing-db"],
    },
}

METRICS = {
    "checkout-api": {"error_rate_pct": 4.2, "p95_latency_ms": 2400, "cpu_pct": 71, "memory_pct": 91, "requests_per_min": 5200,
                     "trend": "Heap usage climbing steadily for 6 hours, a memory-leak pattern"},
    "payments-api": {"error_rate_pct": 18.0, "p95_latency_ms": 900, "cpu_pct": 38, "memory_pct": 55, "requests_per_min": 3100,
                     "trend": "5xx errors jumped from 0.2% to 18% about 20 minutes ago"},
    "inventory-api": {"error_rate_pct": 9.5, "p95_latency_ms": 3100, "cpu_pct": 40, "memory_pct": 62, "requests_per_min": 4100,
                      "db_pool_in_use": "200/200",
                      "trend": "DB connection pool saturated; connections leaking from the warehouse sync job"},
    "search-api": {"error_rate_pct": 6.0, "p95_latency_ms": 1800, "cpu_pct": 88, "memory_pct": 64, "requests_per_min": 9800,
                   "trend": "Worker thread pool saturated; queue depth rising for 40 minutes after a stuck reindex"},
    "orders-api": {"error_rate_pct": 3.1, "p95_latency_ms": 1500, "cpu_pct": 55, "memory_pct": 84, "requests_per_min": 2600,
                   "trend": "Slow memory growth over 12 hours; GC pauses lengthening"},
    "legacy-billing": {"error_rate_pct": 7.0, "p95_latency_ms": 2600, "cpu_pct": 93, "memory_pct": 96, "requests_per_min": 300,
                       "trend": "Invoice batch job exhausting heap on every run since the month-end volume spike"},
}

LOGS = {
    "checkout-api": [
        "T-14m WARN  GC pause 1.8s (G1 Evacuation Pause)",
        "T-9m  ERROR java.lang.OutOfMemoryError: Java heap space at CartSessionCache.put",
        "T-6m  WARN  request /v1/checkout took 2.9s",
        "T-2m  ERROR java.lang.OutOfMemoryError: Java heap space at CartSessionCache.put",
    ],
    "payments-api": [
        "T-21m INFO  deploy 2.14.0 rolled out to 100% of pods",
        "T-20m ERROR NullPointerException at RefundHandler.apply(RefundHandler.java:88) [v2.14.0]",
        "T-18m ERROR 500 POST /v1/refunds correlation=9f2c",
        "T-3m  ERROR NullPointerException at RefundHandler.apply(RefundHandler.java:88) [v2.14.0]",
    ],
    "inventory-api": [
        "T-25m WARN  HikariPool-1 connection leak detection triggered (WarehouseSyncJob)",
        "T-12m ERROR HikariPool-1 - Connection is not available, request timed out after 30000ms",
        "T-4m  ERROR 503 GET /v1/stock/sku-1182",
    ],
    "search-api": [
        "T-41m WARN  reindex job stuck at 63%",
        "T-15m ERROR RejectedExecutionException: queue capacity 2000 reached",
        "T-1m  ERROR RejectedExecutionException: queue capacity 2000 reached",
    ],
    "orders-api": [
        "T-3h  WARN  GC pause 400ms",
        "T-40m WARN  GC pause 900ms",
        "T-5m  ERROR upstream timeout calling orders-db after 5000ms",
    ],
    "legacy-billing": [
        "T-30m INFO  InvoiceBatchJob started (records=1.2M)",
        "T-8m  ERROR java.lang.OutOfMemoryError: GC overhead limit exceeded in InvoiceBatchJob",
        "T-1m  ERROR InvoiceBatchJob aborted; 412k invoices pending",
    ],
}

DEPLOYS = {
    "checkout-api": [{"version": "3.8.2", "minutes_ago": 4320, "author": "ci-bot", "change": "Cart UI copy fixes"},
                     {"version": "3.8.1", "minutes_ago": 10100, "author": "ci-bot", "change": "Dependency bumps"}],
    "payments-api": [{"version": "2.14.0", "minutes_ago": 22, "author": "ci-bot", "change": "Refund flow refactor"},
                     {"version": "2.13.4", "minutes_ago": 4050, "author": "ci-bot", "change": "Ledger client timeout tweak"}],
    "inventory-api": [{"version": "5.2.0", "minutes_ago": 2900, "author": "ci-bot", "change": "Warehouse sync batching"},
                      {"version": "5.1.9", "minutes_ago": 9000, "author": "ci-bot", "change": "Logging"}],
    "search-api": [{"version": "1.22.3", "minutes_ago": 6000, "author": "ci-bot", "change": "Ranking tweak"}],
    "orders-api": [{"version": "4.1.0", "minutes_ago": 9000, "author": "ci-bot", "change": "Order export"}],
    "legacy-billing": [{"version": "0.9.7", "minutes_ago": 40000, "author": "j.doe", "change": "Tax table update"}],
}
