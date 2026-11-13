# TA-o11y-sim-streams

> Part of the **O11y Community Edition** series: free, community-maintained content built on Splunk Observability Cloud data. Not an official Splunk product and not covered by Splunk support.

Curated **Splunk Infrastructure Monitoring (SIM)** data streams for Splunk.

This TA ships **disabled** `sim_modular_input` stanzas with the SignalFlow **baked in** and
key dimensions promoted (`host.name`, instance/database name) for clean identity. It is the
**data-collection layer** that pulls O11y Cloud metrics into a Splunk index — decoupled from
any ITSI content pack.

- **Not ITSI-specific.** The metrics land in `sim_metrics` and are usable from Splunk core
  (search, dashboards, alerts) as much as from ITSI.
- **Domain-agnostic.** OS hosts, cloud instances (AWS EC2, Azure Virtual Machines, Compute Engine), databases (Oracle, PostgreSQL, MSSQL) and AI workloads (GPUs,
  model serving, vector databases, provider tokens) are included today; more domains
  drop in as new `signalflow_programs/<domain>/*.signalflow` files.

## Requirements

- `splunk_ta_sim` installed and licensed (provides the `sim_modular_input` handler).
- An O11y realm/account configured in the SIM add-on
  (Settings → Data Inputs → Splunk Infrastructure Monitoring → account).

## Enable a stream (operator)

1. **Org:** leave `org_id` blank to use the SIM add-on's **default account** (zero config), or
   set `org_id` on the stream to target another org.
2. **Enable:** Settings → Data Inputs → **Splunk Infrastructure Monitoring Data Streams** →
   e.g. `Oracle_DB` / `Postgres_DB` / `MSSQL_DB` → **Enable**.

Streams ship `disabled = 1`, so nothing runs until you enable it.

## Streams shipped

| Domain | Input | Metric prefix | # metrics |
|---|---|---|---|
| os | `OS_Hosts` | OTel host metrics: `cpu.*`, `memory.*`, `disk.summary_utilization`, `system.cpu.load_average.5m`, `system.paging.operations`, `system.disk.*`, `system.network.io`, `system.network.errors` | 12 |
| cloud | `AWS_EC2` | `AWS/EC2` CloudWatch metrics: CPU, network, EBS operations, status checks | 6 |
| cloud | `Azure_VM` | Azure Monitor virtual machine metrics (not validated against live data yet) | 7 |
| cloud | `GCP_Compute` | Compute Engine `instance/*` metrics (not validated against live data yet) | 5 |
| database | `Oracle_DB` | `oracledb.*` | 25 |
| database | `Postgres_DB` | `postgresql.*` | 8 |
| database | `MSSQL_DB` | `sqlserver.*` | 18 |
| ai | `AI_Infrastructure` | `DCGM_FI_DEV_*` (NVIDIA DCGM exporter), `vllm:*`, `milvus_*` | 24 |
| ai | `AI_Providers` | `AWS/Bedrock` CloudWatch metrics, Azure OpenAI metrics | 10 |

`OS_Hosts` reads the Splunk OTel Collector host metrics of every agent host (Linux, Windows, cloud VMs, Kubernetes nodes) at 5-minute resolution (`metric_resolution = 300000`). Each signal is published with an `oshost_*` label and split by `host.name` plus the optional platform dimensions `os.type`, `k8s.node.name`, `k8s.cluster.name`, `cloud.provider`, `cloud.platform`, `cloud.region`, `host.type` and `host.id`. The Infrastructure Monitoring pack uses those dimensions to classify hosts.

The cloud streams read the agentless cloud integrations at the same resolution, with `awsec2_*`, `azvm_*` and `gcpvm_*` labels. Byte and operation counts are 5-minute totals (`rollup='sum'`), whatever the provider's sampling period. `Azure_VM` and `GCP_Compute` follow the metric names of the `splunk_ta_sim` sample inputs and have not been checked against live data yet.

Cumulative counters (for example `oracledb.executions`, `postgresql.commits`, the vLLM `*_total` counters) are requested with `rollup='rate'`, so the index receives per-second values. The SQL Server `*.rate` metrics are raw perf counters despite their gauge type; the database monitoring content pack derives their rates in its base search.

### AI streams

Every AI stream publishes a unique label: `aiinf_*` for `AI_Infrastructure`, `aitok_*` for `AI_Providers`. The SIM add-on keeps the source metric name as `metric_name` and stores the label in the `sf_streamLabel` dimension. Two computations on the same metric (for example Milvus total and failed requests) therefore differ only by `sf_streamLabel`, and searches should filter on it:

```spl
| mstats latest(_value) where index=sim_metrics metric_name=* sf_streamLabel=aiinf_gpu_* by sf_streamLabel UUID span=5m
```

- Histograms (`milvus_proxy_sq_latency`) are read with `histogram(...).sum()` and `.count()`, so each 5-minute point is the interval total.
- Bedrock reports each model both per inference profile and per account; the stream keeps the maximum per `ModelId` to avoid double counting.
- Cloud integration metrics (Bedrock, Azure OpenAI) reach Observability Cloud several minutes late, so `AI_Providers` points trail the other streams.
- No stream collects agent instrumentation metrics (`gen_ai.*`), evaluations or guardrail results. They are out of scope for this add-on.

## Verify (~5 min after enable)

```spl
| mstats latest("oracledb.sessions.usage") as oracle,
         latest("postgresql.backends") as postgres,
         latest("sqlserver.user.connection.count") as mssql
  where index=sim_metrics earliest=-15m by host.name
```

## Adding / editing streams

1. Add or edit `signalflow_programs/<domain>/<name>.signalflow`. Optional leading directives:
   - `# input: <StanzaName>` (default: filename stem, title-cased)
   - `# index: <index>` (default: `sim_metrics`)
   - `# interval: <seconds>` (default: `300`)
   - `# metric_resolution: <milliseconds>` (default: unset, the add-on picks the resolution)
   - `# max_delay: <milliseconds>`, written as `sim_max_delay` (default: unset, Observability Cloud computes it from past delays)
2. Regenerate: `python3 scripts/build_inputs_conf.py`.
3. Never hand-edit `default/inputs.conf` — it is generated.

## Layout

```
TA-o11y-sim-streams/
├── default/
│   ├── app.conf
│   └── inputs.conf                     # GENERATED (disabled stanzas)
├── signalflow_programs/
│   ├── os/os_hosts.signalflow
│   ├── cloud/{aws_ec2,azure_vm,gcp_compute}.signalflow
│   ├── database/{oracle,postgres,mssql}.signalflow
│   └── ai/{ai_infrastructure,ai_providers}.signalflow
├── scripts/build_inputs_conf.py
└── README.md
```

## Relationship to ITSI content packs

ITSI packs (e.g. `DA-ITSI-CP-database-monitoring`) consume the metrics this TA collects. The
pack's KPI base searches read `index=sim_metrics` (via the pack-owned macro
`dbm_sim_metrics_indexes`). Install this TA alongside the pack and enable the matching streams.

| Content pack | Streams |
|---|---|
| Database Monitoring | `Oracle_DB`, `MSSQL_DB`, `Postgres_DB` |
| AI Infrastructure Monitoring | `AI_Infrastructure`, `AI_Providers` |
| Infrastructure Monitoring | `OS_Hosts`, `AWS_EC2`, `Azure_VM`, `GCP_Compute` |

## Troubleshooting

| Symptom | Fix |
|---|---|
| The input log (`splunk_ta_sim_<input>.log`) shows `ANALYTICS_INTERNAL_ERROR` and no data arrives | The add-on resumes each input from its last checkpoint, stored by input name in the `sim_modularinputs` KV collection of `splunk_ta_sim`. An input re-enabled after weeks, or one reusing an old input's name, asks Observability Cloud for the whole gap and fails. Delete the input's record from that collection, then disable and enable the input |
| `'JobStartMessage' object has no attribute 'message'` once per computation start | Logged by the add-on itself; data still flows |
| Points reach the index 10 to 15 minutes late; the input log shows a high `mts_delay` | With the default max delay, Observability Cloud waits for the slowest series, so a few hosts reporting late hold back the whole stream. Set `sim_max_delay` on the input (milliseconds). `OS_Hosts` ships with 60000: points arriving later than 60 seconds are dropped for that interval. Cloud streams keep the default because cloud integrations are normally a few minutes late |
| After a Splunk restart the input logs `HTTP 503 ... KV Store is initializing` and stops | The input started before the KV store was ready and does not retry. Disable and enable the input once the KV store is up |

See [CHANGELOG.md](CHANGELOG.md) for release notes.
