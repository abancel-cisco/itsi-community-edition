# Changelog

## 1.2.0

- `OS_Hosts` rewritten for the Infrastructure Monitoring content pack: twelve Splunk OTel Collector host metrics with `oshost_*` labels at 5-minute resolution, split by `host.name` and optional platform dimensions (OS, Kubernetes node and cluster, cloud provider, platform and region). Replaces the earlier Smart Agent and collectd metrics.
- New `cloud` domain: `AWS_EC2`, `Azure_VM` and `GCP_Compute` streams for agentless cloud instances. Azure and GCP are not validated against live data yet.
- `build_inputs_conf.py` accepts `# metric_resolution:` and `# max_delay:` directives. `OS_Hosts` sets a 60-second max delay so late hosts do not delay the whole stream.

## 1.1.0

- AI streams: `AI_Infrastructure` (NVIDIA DCGM GPUs, vLLM model serving, Milvus vector database) and `AI_Providers` (AWS Bedrock and Azure OpenAI tokens, requests, throttling and latency).
- AI streams publish unique labels, stored as the `sf_streamLabel` dimension, so packs read only their own streams.
- `build_inputs_conf.py` counts `histogram(...)` calls as metrics.

## 1.0.0

- First release: `OS_Hosts`, `Oracle_DB`, `Postgres_DB` and `MSSQL_DB` streams, shipped disabled.
