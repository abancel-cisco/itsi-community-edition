# Changelog

## 1.0.0

- First release: Infrastructure Monitoring (CE) rollup with seven platform services (Linux Hosts (CE), Windows Hosts (CE), Cloud VMs (CE), Kubernetes Nodes (CE), AWS EC2 (CE), Azure Virtual Machines (CE), Google Compute Engine (CE)), each linked to the service template of the same name. The (CE) suffix on services and templates and the O11y prefix on entity types keep every title distinct from stock content packs.
- One entity type per platform, with vitals, a metrics drilldown and a Dashboard Studio entity health dashboard.
- Entity discovery searches driven by macros: index, host name format, discovery scope, platform classification and one inventory macro per source.
- KPIs at 5-minute resolution from the `OS_Hosts`, `AWS_EC2`, `Azure_VM` and `GCP_Compute` streams of `TA-o11y-sim-streams` 1.2.0, plus error and service failure counts from journald and the Windows System event log.
- Part of the O11y Community Edition series; ships only its own service tree.
- Replaces the infrastructure part of the Content Pack for Splunk Observability Cloud and should not run alongside it (a second entity per host and instance); the README lists what to remove first.
- Azure Virtual Machines and Google Compute Engine objects are not tested against live data yet.
