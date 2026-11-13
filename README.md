# Splunk ITSI Community Edition content packs

Free content packs for Splunk IT Service Intelligence (ITSI), generic enough to fit any environment, with the add-ons that feed them. They are community content: not official Splunk products and not covered by Splunk support. Packs built for a specific project or customer are not published here.

The current packs form the **O11y Community Edition** series, built on Splunk Observability Cloud data:

| Folder | What it is | Latest |
|---|---|---|
| [`content-packs/database-monitoring`](content-packs/database-monitoring) | Oracle, Microsoft SQL Server and PostgreSQL instances monitored by the OpenTelemetry collector receivers (`oracledb`, `sqlserver`, `postgresql`), in one service tree | 1.2.0 |
| [`content-packs/ai-infrastructure-monitoring`](content-packs/ai-infrastructure-monitoring) | AI infrastructure (NVIDIA GPUs, vLLM, Milvus) plus token usage and estimated cost of AWS Bedrock and Azure OpenAI: 27 KPIs on linked templates, 4 entity types with health dashboards, scheduled entity imports and an editable model price lookup | 1.0.0 |
| [`content-packs/infrastructure-monitoring`](content-packs/infrastructure-monitoring) | Linux, Windows, cloud VM and Kubernetes hosts, plus AWS EC2, Azure and GCP instances, each with its own service, template and entity type: 5-minute KPIs, optional log KPIs, macro-driven discovery, entity health dashboards. Replaces the infrastructure part of the stock Observability Cloud content pack | 1.0.0 |
| [`add-ons/TA-o11y-sim-streams`](add-ons/TA-o11y-sim-streams) | Curated SignalFlow streams for the Splunk Infrastructure Monitoring Add-on that pull Observability Cloud metrics into the `sim_metrics` index; feeds every pack above | 1.2.0 |
| [`tools`](tools) | Shared REST deployer used by the authoring tools of the AI and Infrastructure Monitoring packs | |

Installable packages are attached to the [GitHub releases](../../releases). The folders hold the sources and the authoring tooling.

## How the pieces fit

1. OpenTelemetry collector receivers, application instrumentation and cloud integrations send metrics to Splunk Observability Cloud.
2. The Splunk Infrastructure Monitoring Add-on (`splunk_ta_sim`) runs the SignalFlow streams shipped in `TA-o11y-sim-streams` and writes the results to the `sim_metrics` metrics index.
3. The content packs build ITSI entities, services and KPIs on top of that index.

Each pack ships only its own service tree. To see them side by side, create a top-level service (for example O11y Community Edition) and add each pack's rollup as a dependency after install.

## Status

Community content with no commercial support: report problems through GitHub issues. The packs are tested on Splunk Enterprise 10.x with ITSI 5.0.x. Test in a non-production environment first.

## License

[Apache License 2.0](LICENSE).
