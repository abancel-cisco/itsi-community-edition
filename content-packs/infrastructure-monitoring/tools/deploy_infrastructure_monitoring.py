#!/usr/bin/env python3
"""Create or update every object of the O11y Infrastructure Monitoring (CE) content pack over REST (authoring tool).

Splunk Observability Cloud is the collector; ITSI gets a 5-minute subset per platform. Data comes from
TA-o11y-sim-streams: OS_Hosts (hosts running the Splunk OTel Collector, labels oshost_*) and the
agentless cloud streams AWS_EC2 (awsec2_*), Azure_VM (azvm_*) and GCP_Compute (gcpvm_*). Optional
system logs (journald, Windows System event log) add error and service failure counts.

Entity discovery is driven by macros: index, host name format, scope filters, platform classification
and one inventory macro per source. Each platform has its own entity type (vitals, entity health
dashboard), discovery search, service template and service, under the Infrastructure Monitoring rollup.

Environment: ITSI_URL, ITSI_TOKEN, ITSI_INSECURE=1 for self-signed management certificates.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import itsi_pack as ip  # noqa: E402

APP, APP_LABEL = "DA-ITSI-CP-infrastructure-monitoring", "O11y Infrastructure Monitoring (CE)"
ROLLUP = {"key": "platmon_rollup", "title": "Infrastructure Monitoring (CE)",
          "description": "Health of every monitored platform: agent hosts by platform and agentless cloud instances."}
IP_RE = r'^\d{1,3}(\.\d{1,3}){3}$'

# ---------------------------------------------------------------- macros

HOST_FORMATS = {
    "fqdn": "eval host_fqdn='host.name', host_name='host.name'",
    "short": (f"eval host_fqdn='host.name', host_name=if(match('host.name', \"{IP_RE}\"), "
              "'host.name', replace('host.name', \"\\..*$\", \"\"))"),
}

AGENT_DIMS = ["os.type", "k8s.node.name", "k8s.cluster.name", "cloud.provider", "cloud.platform",
              "cloud.region", "host.type", "host.id"]
AGENT_INFO = {"os.type": "os_type", "k8s.cluster.name": "k8s_cluster", "cloud.provider": "cloud_provider",
              "cloud.platform": "cloud_platform", "cloud.region": "cloud_region", "host.type": "instance_type",
              "host.id": "host_id"}

MACROS = {
    "infmon_indexes": ("index=sim_metrics", "Metrics index written by the SIM add-on."),
    "infmon_host_name": (HOST_FORMATS["fqdn"],
                          "Entity host name format. host_fqdn keeps host.name as sent; host_name is the title and "
                          "KPI split field. Switch to the short form to strip the domain."),
    "infmon_host_filter": ("search *",
                            "Scope of agent host discovery, applied to the platform dimensions before classification. "
                            "Example: search k8s.cluster.name!=\"dev-*\""),
    "infmon_platform": (
        "eval platform=case(coalesce('k8s.node.name', \"unset\")!=\"unset\", \"kubernetes\", "
        "match('cloud.platform', \"^(aws_ec2|azure_vm|gcp_compute_engine)$\"), \"cloud_vm\", "
        "lower('os.type')=\"windows\", \"windows\", lower('os.type')=\"linux\", \"linux\", true(), \"other\")",
        "Platform classification of agent hosts, first match wins: kubernetes, cloud_vm, windows, linux. "
        "Hosts classified as other are not discovered."),
    "infmon_agent_inventory": (
        "mstats fillnull_value=unset latest(_value) as v where `infmon_indexes` metric_name=* "
        "(sf_streamLabel=oshost_cpus OR sf_streamLabel=oshost_mem_total OR sf_streamLabel=oshost_net_in) by host.name "
        + " ".join(AGENT_DIMS) + " sf_streamLabel "
        "| where 'host.name'!=\"unset\" | `infmon_host_filter` | `infmon_platform` | `infmon_host_name` "
        "| eval cpu=if(sf_streamLabel=\"oshost_cpus\", v, null()), mem=if(sf_streamLabel=\"oshost_mem_total\", v, null()) "
        "| rename " + ", ".join(f"{d} as {f}" for d, f in AGENT_INFO.items()) + " "
        "| stats first(platform) as platform " + " ".join(f"first({f}) as {f}" for f in AGENT_INFO.values())
        + " max(cpu) as cpu_count max(mem) as mem_bytes count(eval(sf_streamLabel=\"oshost_net_in\")) as host_metrics "
        "by host_name host_fqdn | where host_metrics>0 "
        f"| eval memory_total_gb=round(mem_bytes/1073741824, 1), host_short=if(match(host_fqdn, \"{IP_RE}\"), "
        "host_fqdn, lower(replace(host_fqdn, \"\\..*$\", \"\"))), entity_title=host_name "
        "| foreach " + " ".join(AGENT_INFO.values()) + " [eval <<FIELD>>=if('<<FIELD>>'=\"unset\", null(), '<<FIELD>>')]",
        "One row per agent host with its platform and inventory fields. Hosts without OTel host metrics "
        "(system.network.io) are skipped. Used by the agent host discovery searches."),
    "infmon_aws_ec2_filter": ("search *", "Scope of AWS EC2 discovery. Example: search aws_region=eu-*"),
    "infmon_aws_ec2_inventory": (
        "mstats latest(_value) as v where `infmon_indexes` metric_name=* sf_streamLabel=awsec2_cpu by InstanceId AWSUniqueId "
        "| eval parts=split(AWSUniqueId, \"_\"), aws_instance_id=InstanceId, aws_region=mvindex(parts, 1), "
        "aws_account_id=mvindex(parts, 2), entity_title=InstanceId "
        "| `infmon_aws_ec2_filter` | stats first(entity_title) as entity_title first(aws_region) as aws_region "
        "first(aws_account_id) as aws_account_id by aws_instance_id",
        "One row per EC2 instance (title InstanceId, region and account from AWSUniqueId)."),
    "infmon_azure_vm_filter": ("search *", "Scope of Azure VM discovery. Example: search azure_region=westeurope"),
    "infmon_azure_vm_inventory": (
        "mstats fillnull_value=unset latest(_value) as v where `infmon_indexes` metric_name=* sf_streamLabel=azvm_cpu "
        "by azure_resource_id azure_resource_name azure_region resource_group subscription_id "
        "| `infmon_azure_vm_filter` "
        "| eval entity_title=if(azure_resource_name!=\"unset\", azure_resource_name, azure_resource_id) "
        "| stats first(entity_title) as entity_title first(azure_region) as azure_region first(resource_group) as resource_group "
        "first(subscription_id) as subscription_id by azure_resource_id",
        "One row per Azure virtual machine (title azure_resource_name)."),
    "infmon_gcp_vm_filter": ("search *", "Scope of Compute Engine discovery. Example: search project_id=prod-*"),
    "infmon_gcp_vm_inventory": (
        "mstats fillnull_value=unset latest(_value) as v where `infmon_indexes` metric_name=* sf_streamLabel=gcpvm_cpu "
        "by instance_id instance_name project_id zone "
        "| `infmon_gcp_vm_filter` "
        "| eval gcp_instance_id=instance_id, entity_title=if(instance_name!=\"unset\", instance_name, instance_id) "
        "| stats first(entity_title) as entity_title first(project_id) as project_id first(zone) as zone by gcp_instance_id",
        "One row per Compute Engine instance (title instance_name)."),
    "infmon_linux_log_indexes": ("index=linux", "Indexes holding Linux system logs (journald)."),
    "infmon_windows_log_indexes": ("index=wineventlog", "Indexes holding the Windows System event log."),
    "infmon_log_indexes": ("(`infmon_linux_log_indexes`) OR (`infmon_windows_log_indexes`)",
                           "Every system log index, for ad-hoc searches. Set the two platform macros instead."),
    "infmon_linux_logs": (
        "search `infmon_linux_log_indexes` sourcetype=journald "
        "| eval log_error=if(tonumber(PRIORITY)<=3, 1, 0), "
        "svc_failure=if(match(MESSAGE, \"Scheduled restart job|Failed with result|Main process exited, code=(killed|dumped)\"), 1, 0)",
        "Linux system log events. log_error: journald priority err or worse. svc_failure: systemd unit failures and restarts."),
    "infmon_windows_logs": (
        "search `infmon_windows_log_indexes` (sourcetype=WinEventLog OR sourcetype=XmlWinEventLog OR sourcetype=\"WinEventLog:System\" "
        "OR sourcetype=\"XmlWinEventLog:System\") source=\"*WinEventLog:System\" "
        "| eval log_error=if(in(Type, \"Error\", \"Critical\") OR in(Level, \"1\", \"2\"), 1, 0), "
        "svc_failure=if(in(EventCode, \"7023\", \"7024\", \"7031\", \"7034\"), 1, 0)",
        "Windows System event log. log_error: Error and Critical events. svc_failure: Service Control Manager "
        "events 7023, 7024, 7031 and 7034 (service terminated or failed)."),
    "infmon_log_host": (f"eval host_short=if(match(host, \"{IP_RE}\"), host, lower(replace(host, \"\\..*$\", \"\")))",
                         "Maps the log host field to the host_short entity alias."),
}

# ---------------------------------------------------------------- base searches

AGENT_COLUMNS = (
    "cpu_pct=round(oshost_cpu, 2), mem_pct=round(oshost_mem, 2), disk_pct=round(oshost_disk, 2), "
    "load_per_cpu=if(oshost_cpus>0, round(oshost_load5/oshost_cpus, 2), null()), "
    "paging_s=round(oshost_paging, 2), disk_ops_s=round(oshost_disk_ops, 2), disk_io_bs=round(oshost_disk_io, 0), "
    "net_in_bs=round(oshost_net_in, 0), net_out_bs=round(oshost_net_out, 0), net_errors=oshost_net_errors"
)


def pivot(label_glob: str, by: str, rename: str = "") -> str:
    prefix = label_glob.rstrip("*")
    return (f"| mstats latest(_value) as v where `infmon_indexes` metric_name=* sf_streamLabel={label_glob} "
            f"by {by} sf_streamLabel span=5m {rename}"
            f"| eval {{sf_streamLabel}}=v ")


def total(a: str, b: str, div: str = "") -> str:
    return f"if(isnull({a}) AND isnull({b}), null(), round((coalesce({a}, 0)+coalesce({b}, 0)){div}, 2))"


BASE_SEARCHES = {
    "platmon_bs_agent": {
        "title": "Infrastructure Monitoring - Agent Hosts", "split": "host_name",
        "spl": pivot("oshost_*", "host.name") + "| `infmon_host_name` "
               "| stats first(oshost_*) as oshost_* by _time host_name "
               f"| eval {AGENT_COLUMNS} "
               "| fields _time host_name cpu_pct mem_pct disk_pct load_per_cpu paging_s disk_ops_s disk_io_bs "
               "net_in_bs net_out_bs net_errors",
    },
    "platmon_bs_logs": {
        "title": "Infrastructure Monitoring - System Logs", "split": "host_short",
        "spl": "| multisearch [ `infmon_linux_logs` ] [ `infmon_windows_logs` ] | `infmon_log_host` "
               "| bin _time span=5m | stats sum(log_error) as log_errors sum(svc_failure) as svc_failures by _time host_short",
    },
    "platmon_bs_aws_ec2": {
        "lag": "600",
        "title": "Infrastructure Monitoring - AWS EC2", "split": "aws_instance_id",
        "spl": pivot("awsec2_*", "InstanceId", "| rename InstanceId as aws_instance_id ")
               + "| stats first(awsec2_*) as awsec2_* by _time aws_instance_id "
               "| eval cpu_pct=round(awsec2_cpu, 2), net_in_bs=round(awsec2_net_in/300, 0), "
               "net_out_bs=round(awsec2_net_out/300, 0), "
               f"ebs_ops_s={total('awsec2_ebs_read_ops', 'awsec2_ebs_write_ops', '/300')}, "
               "status_failed=awsec2_status_failed "
               "| fields _time aws_instance_id cpu_pct net_in_bs net_out_bs ebs_ops_s status_failed",
    },
    "platmon_bs_azure_vm": {
        "lag": "600",
        "title": "Infrastructure Monitoring - Azure Virtual Machines", "split": "azure_resource_id",
        "spl": pivot("azvm_*", "azure_resource_id")
               + "| stats first(azvm_*) as azvm_* by _time azure_resource_id "
               "| eval cpu_pct=round(azvm_cpu, 2), mem_avail_gb=round(azvm_mem_avail/1073741824, 2), "
               "net_in_bs=round(azvm_net_in/300, 0), net_out_bs=round(azvm_net_out/300, 0), "
               f"disk_ops_s={total('azvm_disk_read_ops', 'azvm_disk_write_ops')}, "
               "vm_unavailable=if(isnull(azvm_available), null(), if(azvm_available<1, 1, 0)) "
               "| fields _time azure_resource_id cpu_pct mem_avail_gb net_in_bs net_out_bs disk_ops_s vm_unavailable",
    },
    "platmon_bs_gcp_vm": {
        "lag": "600",
        "title": "Infrastructure Monitoring - Google Compute Engine", "split": "gcp_instance_id",
        "spl": pivot("gcpvm_*", "instance_id", "| rename instance_id as gcp_instance_id ")
               + "| stats first(gcpvm_*) as gcpvm_* by _time gcp_instance_id "
               "| eval cpu_pct=round(gcpvm_cpu, 2), net_in_bs=round(gcpvm_net_in/300, 0), "
               "net_out_bs=round(gcpvm_net_out/300, 0), "
               f"disk_ops_s={total('gcpvm_disk_read_ops', 'gcpvm_disk_write_ops', '/300')} "
               "| fields _time gcp_instance_id cpu_pct net_in_bs net_out_bs disk_ops_s",
    },
}

# ---------------------------------------------------------------- KPIs
# field: (base search, title, unit, entity statop, service statop, levels, importance, gauge max, description)

PCT = [("medium", 80), ("high", 90), ("critical", 95)]
KPI_SPECS = {
    "cpu_pct": ("platmon_bs_agent", "CPU Utilization %", "%", "avg", "avg", PCT, 7, 100, "cpu.utilization, all cores."),
    "mem_pct": ("platmon_bs_agent", "Memory Used %", "%", "avg", "avg", [("medium", 85), ("high", 90), ("critical", 95)],
                8, 100, "memory.utilization."),
    "disk_pct": ("platmon_bs_agent", "Disk Used %", "%", "max", "avg", PCT, 9, 100,
                 "disk.summary_utilization: used share of local filesystem capacity."),
    "load_per_cpu": ("platmon_bs_agent", "Load per CPU (5m)", "", "avg", "avg",
                     [("medium", 1), ("high", 1.5), ("critical", 2)], 5, 4,
                     "system.cpu.load_average.5m divided by cpu.num_processors, so one threshold fits every host size."),
    "paging_s": ("platmon_bs_agent", "Paging Operations /s", "ops/s", "avg", "avg", [], 3, 0,
                 "system.paging.operations, pages swapped in and out per second. No default thresholds."),
    "disk_ops_s": ("platmon_bs_agent", "Disk Operations /s", "ops/s", "avg", "sum", [], 1, 0,
                   "system.disk.operations, reads and writes per second. No default thresholds."),
    "disk_io_bs": ("platmon_bs_agent", "Disk I/O", "B/s", "avg", "sum", [], 1, 0,
                   "system.disk.io.total, bytes read and written per second. No default thresholds."),
    "net_in_bs": ("platmon_bs_agent", "Network In", "B/s", "avg", "sum", [], 1, 0,
                  "system.network.io received, bytes per second. No default thresholds."),
    "net_out_bs": ("platmon_bs_agent", "Network Out", "B/s", "avg", "sum", [], 1, 0,
                   "system.network.io transmitted, bytes per second. No default thresholds."),
    "net_errors": ("platmon_bs_agent", "Network Errors", "errors", "sum", "sum", [("medium", 1), ("high", 100)], 5, 200,
                   "system.network.errors, all interfaces and directions, per 5 minutes."),
    "log_errors": ("platmon_bs_logs", "Error Log Events", "events", "sum", "sum", [("medium", 10), ("high", 50)], 5, 100,
                   "System log events at error severity or worse, per 5 minutes (infmon_linux_logs, infmon_windows_logs)."),
    "svc_failures": ("platmon_bs_logs", "Service Failures", "events", "sum", "sum", [("medium", 1), ("high", 3)], 6, 10,
                     "Service crashes, failures and automatic restarts in the system log, per 5 minutes."),
}
CLOUD_SPECS = {
    "aws_ec2": {
        "cpu_pct": ("CPU Utilization %", "%", "avg", "avg", PCT, 7, 100, "CloudWatch CPUUtilization."),
        "net_in_bs": ("Network In", "B/s", "avg", "sum", [], 1, 0, "CloudWatch NetworkIn, bytes per second."),
        "net_out_bs": ("Network Out", "B/s", "avg", "sum", [], 1, 0, "CloudWatch NetworkOut, bytes per second."),
        "ebs_ops_s": ("EBS Operations /s", "ops/s", "avg", "sum", [], 1, 0, "CloudWatch EBSReadOps plus EBSWriteOps per second."),
        "status_failed": ("Status Check Failed", "", "max", "max", [("critical", 1)], 9, 1,
                          "CloudWatch StatusCheckFailed (instance or system check), 1 when failing."),
    },
    "azure_vm": {
        "cpu_pct": ("CPU Utilization %", "%", "avg", "avg", PCT, 7, 100, "Azure Monitor Percentage CPU."),
        "mem_avail_gb": ("Available Memory", "GB", "avg", "avg", [], 3, 0, "Azure Monitor Available Memory Bytes, in GB."),
        "net_in_bs": ("Network In", "B/s", "avg", "sum", [], 1, 0, "Azure Monitor Network In, bytes per second."),
        "net_out_bs": ("Network Out", "B/s", "avg", "sum", [], 1, 0, "Azure Monitor Network Out, bytes per second."),
        "disk_ops_s": ("Disk Operations /s", "ops/s", "avg", "sum", [], 1, 0, "Azure Monitor disk read plus write operations per second."),
        "vm_unavailable": ("VM Unavailable", "", "max", "max", [("critical", 1)], 9, 1,
                           "1 when Azure Monitor VmAvailabilityMetric reports the VM as unavailable."),
    },
    "gcp_vm": {
        "cpu_pct": ("CPU Utilization %", "%", "avg", "avg", PCT, 7, 100, "instance/cpu/utilization, in percent."),
        "net_in_bs": ("Network In", "B/s", "avg", "sum", [], 1, 0, "instance/network/received_bytes_count, bytes per second."),
        "net_out_bs": ("Network Out", "B/s", "avg", "sum", [], 1, 0, "instance/network/sent_bytes_count, bytes per second."),
        "disk_ops_s": ("Disk Operations /s", "ops/s", "avg", "sum", [], 1, 0, "instance/disk read plus write operations per second."),
    },
}

# ---------------------------------------------------------------- dashboards


def agent_metric(labels: dict) -> str:
    where = " OR ".join(f"sf_streamLabel={lbl}" for lbl in labels)
    names = ", ".join(f'{lbl} as "{title}"' for lbl, title in labels.items())
    return (f'| mstats avg(_value) as v where `infmon_indexes` metric_name=* "host.name"="$host_fqdn$" ({where}) '
            f"by sf_streamLabel span=5m | xyseries _time sf_streamLabel v | rename {names}")


def cloud_metric(dim: str, token: str, labels: dict, scale: str = "") -> str:
    where = " OR ".join(f"sf_streamLabel={lbl}" for lbl in labels)
    names = ", ".join(f'{lbl} as "{title}"' for lbl, title in labels.items())
    return (f'| mstats avg(_value) as v where `infmon_indexes` metric_name=* {dim}="${token}$" ({where}) '
            f"by sf_streamLabel span=5m {scale}| xyseries _time sf_streamLabel v | rename {names}")


LOG_COUNTS = ("| multisearch [ `infmon_linux_logs` ] [ `infmon_windows_logs` ] | `infmon_log_host` "
              '| search host_short="$host_short$" | timechart span=5m sum(log_error) as "Error events" '
              'sum(svc_failure) as "Service failures"')
LOG_EVENTS = ("| multisearch [ `infmon_linux_logs` ] [ `infmon_windows_logs` ] | `infmon_log_host` "
              '| search host_short="$host_short$" (log_error=1 OR svc_failure=1) '
              "| eval message=coalesce(MESSAGE, Message, _raw), kind=if(svc_failure=1, \"service failure\", \"error\") "
              "| head 50 | table _time kind sourcetype message")

AGENT_PANELS = {
    "cpu_mem": ("CPU and memory %", "line", agent_metric({"oshost_cpu": "CPU %", "oshost_mem": "Memory %"})),
    "disk": ("Disk used %", "line", agent_metric({"oshost_disk": "Disk used %"})),
    "load": ("Load average (5m) and CPU count", "line", agent_metric({"oshost_load5": "Load 5m", "oshost_cpus": "CPUs"})),
    "net": ("Network B/s", "line", agent_metric({"oshost_net_in": "In", "oshost_net_out": "Out"})),
    "disk_ops": ("Disk operations /s", "line", agent_metric({"oshost_disk_ops": "Ops/s"})),
    "disk_io": ("Disk I/O B/s", "line", agent_metric({"oshost_disk_io": "Bytes/s"})),
    "paging": ("Paging operations /s", "line", agent_metric({"oshost_paging": "Ops/s"})),
    "net_errors": ("Network errors per 5 minutes", "column", agent_metric({"oshost_net_errors": "Errors"})),
    "log_counts": ("System log errors and service failures", "column", LOG_COUNTS),
    "log_events": ("Latest error and service failure events", "table", LOG_EVENTS),
}

# ---------------------------------------------------------------- components

AGENT_KPIS = ["cpu_pct", "mem_pct", "disk_pct", "load_per_cpu", "paging_s", "disk_ops_s", "disk_io_bs",
              "net_in_bs", "net_out_bs", "net_errors"]
LOG_KPIS = ["log_errors", "svc_failures"]
AGENT_VITALS = [("CPU Utilization %", "oshost_cpu", "%"), ("Memory Used %", "oshost_mem", "%"),
                ("Disk Used %", "oshost_disk", "%")]
AGENT_IDS = ["host_name", "host_fqdn", "host_short"]

COMPONENTS = [
    {"key": "linux", "platform": "linux", "svc": "Linux Hosts (CE)", "etype": "O11y Linux Host",
     "etype_description": "Linux host running the Splunk OTel Collector (Splunk Observability Cloud).",
     "kpis": AGENT_KPIS + LOG_KPIS, "info": ["platform", "os_type", "cpu_count", "memory_total_gb"],
     "panels": list(AGENT_PANELS)},
    {"key": "windows", "platform": "windows", "svc": "Windows Hosts (CE)", "etype": "O11y Windows Host",
     "etype_description": "Windows host running the Splunk OTel Collector (Splunk Observability Cloud).",
     "kpis": [k for k in AGENT_KPIS if k != "load_per_cpu"] + LOG_KPIS,
     "info": ["platform", "os_type", "cpu_count", "memory_total_gb"],
     "panels": [p for p in AGENT_PANELS if p != "load"]},
    {"key": "cloud_vm", "platform": "cloud_vm", "svc": "Cloud VMs (CE)", "etype": "O11y Cloud VM",
     "etype_description": "AWS, Azure or Google Cloud virtual machine running the Splunk OTel Collector.",
     "kpis": AGENT_KPIS + LOG_KPIS,
     "info": ["platform", "os_type", "cloud_provider", "cloud_platform", "cloud_region", "instance_type", "host_id",
              "cpu_count", "memory_total_gb"],
     "panels": list(AGENT_PANELS)},
    {"key": "k8s_node", "platform": "kubernetes", "svc": "Kubernetes Nodes (CE)", "etype": "O11y Kubernetes Node",
     "etype_description": "Kubernetes node monitored by the Splunk OTel Collector agent.",
     "kpis": AGENT_KPIS, "info": ["platform", "os_type", "k8s_cluster", "cloud_provider", "cloud_region",
                                  "cpu_count", "memory_total_gb"],
     "panels": [p for p in AGENT_PANELS if not p.startswith("log_")]},
    {"key": "aws_ec2", "svc": "AWS EC2 (CE)", "etype": "O11y AWS EC2 Instance", "bs": "platmon_bs_aws_ec2",
     "etype_description": "EC2 instance from the Splunk Observability Cloud AWS integration (CloudWatch, agentless).",
     "id": "aws_instance_id", "inventory": "infmon_aws_ec2_inventory", "info": ["aws_region", "aws_account_id"],
     "drilldown": ("InstanceId", "awsec2_*"),
     "vitals": [("CPU Utilization %", "awsec2_cpu", "%")],
     "panels": {"cpu": ("CPU utilization %", "line", cloud_metric("InstanceId", "aws_instance_id", {"awsec2_cpu": "CPU %"})),
                "status": ("Status check failed", "column", cloud_metric("InstanceId", "aws_instance_id", {"awsec2_status_failed": "Failed"})),
                "net": ("Network B/s", "line", cloud_metric("InstanceId", "aws_instance_id",
                                                            {"awsec2_net_in": "In", "awsec2_net_out": "Out"}, "| eval v=v/300 ")),
                "ebs": ("EBS operations /s", "line", cloud_metric("InstanceId", "aws_instance_id",
                                                                  {"awsec2_ebs_read_ops": "Read", "awsec2_ebs_write_ops": "Write"}, "| eval v=v/300 "))}},
    {"key": "azure_vm", "svc": "Azure Virtual Machines (CE)", "etype": "O11y Azure Virtual Machine", "bs": "platmon_bs_azure_vm",
     "etype_description": "Azure virtual machine from the Splunk Observability Cloud Azure integration (agentless).",
     "id": "azure_resource_id", "inventory": "infmon_azure_vm_inventory",
     "info": ["azure_region", "resource_group", "subscription_id"], "drilldown": ("azure_resource_id", "azvm_*"),
     "vitals": [("CPU Utilization %", "azvm_cpu", "%")],
     "panels": {"cpu": ("CPU utilization %", "line", cloud_metric("azure_resource_id", "azure_resource_id", {"azvm_cpu": "CPU %"})),
                "mem": ("Available memory GB", "line", cloud_metric("azure_resource_id", "azure_resource_id",
                                                                    {"azvm_mem_avail": "Available GB"}, "| eval v=v/1073741824 ")),
                "net": ("Network B/s", "line", cloud_metric("azure_resource_id", "azure_resource_id",
                                                            {"azvm_net_in": "In", "azvm_net_out": "Out"}, "| eval v=v/300 ")),
                "disk": ("Disk operations /s", "line", cloud_metric("azure_resource_id", "azure_resource_id",
                                                                    {"azvm_disk_read_ops": "Read", "azvm_disk_write_ops": "Write"}))}},
    {"key": "gcp_vm", "svc": "Google Compute Engine (CE)", "etype": "O11y Google Compute Engine Instance", "bs": "platmon_bs_gcp_vm",
     "etype_description": "Compute Engine instance from the Splunk Observability Cloud GCP integration (agentless).",
     "id": "gcp_instance_id", "inventory": "infmon_gcp_vm_inventory", "info": ["project_id", "zone"],
     "drilldown": ("instance_id", "gcpvm_*"),
     "vitals": [("CPU Utilization %", "gcpvm_cpu", "%")],
     "panels": {"cpu": ("CPU utilization %", "line", cloud_metric("instance_id", "gcp_instance_id", {"gcpvm_cpu": "CPU %"})),
                "net": ("Network B/s", "line", cloud_metric("instance_id", "gcp_instance_id",
                                                            {"gcpvm_net_in": "In", "gcpvm_net_out": "Out"}, "| eval v=v/300 ")),
                "disk": ("Disk operations /s", "line", cloud_metric("instance_id", "gcp_instance_id",
                                                                    {"gcpvm_disk_read_ops": "Read", "gcpvm_disk_write_ops": "Write"}, "| eval v=v/300 "))}},
]

SEVERITY = {"low": (3, "#FFE98C", "#FFF4C5"), "medium": (4, "#FCB64E", "#FEE6C1"),
            "high": (5, "#F26A35", "#FBCBB9"), "critical": (6, "#B50101", "#E5A6A6")}


def is_agent(comp: dict) -> bool:
    return "platform" in comp


def svc_key(comp):
    return f"platmon_svc_{comp['key']}"


def template_key(comp):
    return f"platmon_tpl_{comp['key']}"


def etype_key(comp):
    return f"platmon_et_{comp['key']}"


def template_title(comp):
    return comp["svc"]


def import_name(comp):
    return f"INFMON Discover Entities - {comp['svc']}"


def dashboard_id(comp):
    return f"infmon_entity_{comp['key']}"


def kpi_specs(comp: dict) -> list:
    """(field, base search key, split field, title, unit, entity, service, levels, importance, gauge max, description)"""
    if is_agent(comp):
        out = []
        for f in comp["kpis"]:
            bs, *rest = KPI_SPECS[f]
            out.append((f, bs, BASE_SEARCHES[bs]["split"], *rest))
        return out
    bs = comp["bs"]
    return [(f, bs, BASE_SEARCHES[bs]["split"], *spec) for f, spec in CLOUD_SPECS[comp["key"]].items()]


def lag(bs: str) -> str:
    """Seconds the KPI search waits for data: cloud integrations reach Observability Cloud minutes late."""
    return BASE_SEARCHES[bs].get("lag", "90")


def base_search_metrics(bs: str) -> list:
    seen = {}
    for comp in COMPONENTS:
        for f, b, _, title, unit, ent, agg, *_ in kpi_specs(comp):
            if b == bs:
                seen.setdefault(f, (title, unit, ent, agg))
    return [{"_key": f, "title": t, "threshold_field": f, "unit": u, "entity_statop": e, "aggregate_statop": a,
             "fill_gaps": "null_value", "gap_severity": "info", "gap_severity_value": "1",
             "gap_severity_color": "#AED3E5", "gap_severity_color_light": "#E3F0F6"}
            for f, (t, u, e, a) in seen.items()]

# ---------------------------------------------------------------- deploy steps


def ensure_macros(reset: bool, host_format: str | None):
    ip.ensure_app({"app": APP, "app_label": APP_LABEL, "macro": "infmon_indexes"})
    base = f"/servicesNS/nobody/{APP}/configs/conf-macros"
    for name, (definition, description) in MACROS.items():
        if name == "infmon_host_name" and host_format:
            definition = HOST_FORMATS[host_format]
        if not ip.exists(f"{base}/{name}"):
            ip.form("POST", base, {"name": name, "definition": definition, "description": description})
            print(f"  created macro {name}")
        elif reset or (name == "infmon_host_name" and host_format):
            ip.form("POST", f"{base}/{name}", {"definition": definition, "description": description})
            print(f"  reset macro {name}")
        ip.form("POST", f"{base}/{name}/acl", {"owner": "nobody", "sharing": "global", "perms.read": "*"})


def upsert_base_searches():
    for key, bs in BASE_SEARCHES.items():
        split = bs["split"]
        payload = {
            "_key": key, "title": bs["title"], "description": f"{bs['title']}: KPI columns split by {split}.",
            "base_search": bs["spl"], "search_alert_earliest": "5", "alert_period": "5", "alert_lag": lag(key),
            "is_metric": False, "metric_qualifier": "", "source_itsi_da": "", "sec_grp": ip.TEAM,
            "is_entity_breakdown": True, "is_service_entity_filter": True,
            "is_split_by_entity": True, "is_filter_entities_to_service": True,
            "entity_breakdown_id_fields": split, "entity_alias_filtering_fields": split,
            "entity_id_fields": split, "entity_filter_field": split, "entity_split_field": split,
            "metrics": base_search_metrics(key),
        }
        path = f"{ip.ITOA}/kpi_base_search/{key}"
        existing = ip.get(path)
        ip.post(path if existing else f"{ip.ITOA}/kpi_base_search", {**(existing or {}), **payload})
        print(f"  {'updated' if existing else 'created'} base search {bs['title']}")


def discovery_spl(comp: dict) -> str:
    if is_agent(comp):
        fields = " ".join(["entity_title"] + AGENT_IDS + ["etype"] + comp["info"])
        return (f'| `infmon_agent_inventory` | where platform="{comp["platform"]}" '
                f'| eval etype="{comp["etype"]}" | table {fields}')
    fields = " ".join(["entity_title", comp["id"], "etype"] + comp["info"])
    return f'| `{comp["inventory"]}` | eval etype="{comp["etype"]}" | table {fields}'


def upsert_discovery(comp: dict, minute: int):
    ids = AGENT_IDS if is_agent(comp) else [comp["id"]]
    fields = {
        "search": discovery_spl(comp),
        "description": f"Discovers {comp['etype']} entities. Scope and classification come from the infmon_* macros.",
        "dispatch.earliest_time": "-4h", "dispatch.latest_time": "now",
        "cron_schedule": f"{minute} * * * *", "is_scheduled": "1", "disabled": "0",
        "actions": "itsi_import_objects", "action.itsi_import_objects": "1",
        "action.itsi_import_objects.param.backfill_enabled": "0",
        "action.itsi_import_objects.param.entity_identifier_fields": ",".join(ids),
        "action.itsi_import_objects.param.entity_informational_fields": ",".join(["etype"] + comp["info"]),
        "action.itsi_import_objects.param.entity_merge_field": "host_fqdn" if is_agent(comp) else comp["id"],
        "action.itsi_import_objects.param.entity_title_field": "entity_title",
        "action.itsi_import_objects.param.entity_type_field": "etype",
        "action.itsi_import_objects.param.service_enabled": "1",
        "action.itsi_import_objects.param.service_team": ip.TEAM,
        "action.itsi_import_objects.param.service_templates_config": "{}",
        "action.itsi_import_objects.param.update_type": "upsert",
        "alert.track": "0",
    }
    name, base = import_name(comp), f"/servicesNS/nobody/{APP}/saved/searches"
    if ip.exists(f"{base}/{ip.q(name)}"):
        ip.form("POST", f"{base}/{ip.q(name)}", fields)
    else:
        ip.form("POST", base, {"name": name, **fields})
    ip.form("POST", f"{base}/{ip.q(name)}/acl", {"owner": "nobody", "sharing": "global", "perms.read": "*"})
    print(f"  discovery search {name}")


def panels(comp: dict) -> list:
    if is_agent(comp):
        return [AGENT_PANELS[p] for p in comp["panels"]]
    return list(comp["panels"].values())


def dashboard_definition(comp: dict) -> dict:
    viz, ds, layout, y, col = {}, {}, [], 0, 0
    for i, (title, kind, query) in enumerate(panels(comp)):
        full = kind == "table"
        if full and col:
            y, col = y + 260, 0
        viz[f"viz_{i}"] = {"dataSources": {"primary": f"ds_{i}"}, "title": title, "type": f"splunk.{kind}",
                           "options": {} if full else {"nullValueDisplay": "gap", "legendDisplay": "bottom"}}
        ds[f"ds_{i}"] = {"name": f"ds_{i}", "type": "ds.search", "options": {"query": query}}
        layout.append({"item": f"viz_{i}", "type": "block",
                       "position": {"x": col * 600, "y": y, "w": 1180 if full else 580, "h": 300 if full else 250}})
        if full or col:
            y, col = y + (310 if full else 260), 0
        else:
            col = 1
    return {
        "title": f"{comp['etype']} Entity Health",
        "description": f"Splunk Observability Cloud metrics for {comp['etype']} entities, 5-minute resolution.",
        "inputs": {},
        "defaults": {"dataSources": {"ds.search": {"options": {"queryParameters": {
            "earliest": "$global_time.earliest$", "latest": "$global_time.latest$"}}}}},
        "visualizations": viz, "dataSources": ds,
        "layout": {"type": "grid", "options": {"height": y + (260 if col else 0), "width": 1200},
                   "structure": layout, "globalInputs": []},
    }


def upsert_dashboard(comp: dict):
    view, label = dashboard_id(comp), f"{comp['etype']} Entity Health"
    xml = (f'<dashboard version="2" theme="light">\n    <label>{label}</label>\n'
           f'    <description>ITSI entity health for {comp["etype"]}</description>\n'
           f'    <definition><![CDATA[\n{json.dumps(dashboard_definition(comp), indent=4)}\n]]></definition>\n'
           f'</dashboard>\n')
    base = f"/servicesNS/nobody/{APP}/data/ui/views"
    if ip.exists(f"{base}/{view}"):
        ip.form("POST", f"{base}/{view}", {"eai:data": xml})
    else:
        ip.form("POST", base, {"name": view, "eai:data": xml})
    ip.form("POST", f"{base}/{view}/acl", {"owner": "nobody", "sharing": "global", "perms.read": "*",
                                            "perms.write": "admin,power"})
    print(f"  dashboard {label}")


def upsert_entity_type(comp: dict):
    if is_agent(comp):
        vitals, match = AGENT_VITALS, "host_name"
        params = [{"alias": "host_fqdn", "param": "host_fqdn"}, {"alias": "host_short", "param": "host_short"}]
        drill = {"title": "O11y host metrics (Splunk)", "type": "metrics",
                 "static_filter": {"type": "include", "field": "sf_streamLabel", "values": ["oshost_*"]},
                 "entity_field_filter": {"type": "entity", "data_field": "host.name", "entity_field": "host_fqdn"}}
    else:
        vitals, match = comp["vitals"], comp["id"]
        params = [{"alias": comp["id"], "param": comp["id"]}]
        drill = {"title": "O11y cloud metrics (Splunk)", "type": "metrics",
                 "static_filter": {"type": "include", "field": "sf_streamLabel", "values": [comp["drilldown"][1]]},
                 "entity_field_filter": {"type": "entity", "data_field": comp["drilldown"][0], "entity_field": comp["id"]}}
    by = "host.name" if is_agent(comp) else comp["drilldown"][0]
    post = "| `infmon_host_name` " if is_agent(comp) else f"| rename {by} as {match} "
    payload = {
        "_key": etype_key(comp), "title": comp["etype"], "identifying_name": comp["etype"].lower(),
        "description": comp["etype_description"],
        "show_dashboards_in_service_analyzer": True, "show_navigation_in_service_analyzer": True,
        "data_drilldowns": [drill],
        "dashboard_drilldowns": [{
            "title": f"{comp['etype']} Entity Health", "id": dashboard_id(comp), "base_url": "",
            "dashboard_type": "udf_dashboard",
            "params": {"static_params": {}, "alias_param_map": params + [
                {"alias": "time_range_token-2", "param": "global_time"}]},
        }],
        "vital_metrics": [{
            "is_key": 1 if i == 0 else 0, "metric_name": title, "unit": unit,
            "matching_entity_fields": [match], "split_by_fields": [match],
            "search": (f"| mstats avg(_value) as val where `infmon_indexes` metric_name=* sf_streamLabel={label} "
                       f'by "{by}" span=5m {post}| table _time val {match}'),
        } for i, (title, label, unit) in enumerate(vitals)],
    }
    path = f"{ip.ITOA}/entity_type/{etype_key(comp)}"
    existing = ip.get(path)
    ip.post(path if existing else f"{ip.ITOA}/entity_type", {**(existing or {}), **payload})
    print(f"  {'updated' if existing else 'created'} entity type {comp['etype']}")


def thresholds(field: str, levels: list, gauge_max: float) -> dict:
    t = ip.thresholds(field)
    if gauge_max:
        t.update(gaugeMax=gauge_max, renderBoundaryMax=gauge_max)
    t["thresholdLevels"] = [{
        "severityValue": SEVERITY[sev][0], "severityLabel": sev, "severityColor": SEVERITY[sev][1],
        "severityColorLight": SEVERITY[sev][2], "thresholdValue": value, "dynamicParam": 0,
    } for sev, value in levels]
    return t


def make_kpi(comp: dict, spec: tuple) -> dict:
    field, bs, split, title, unit, ent, agg, levels, importance, gmax, desc = spec
    spl = BASE_SEARCHES[bs]["spl"]
    return {
        "_key": f"platmon_{comp['key']}_{field}", "title": title, "description": desc, "unit": unit,
        "search_type": "shared_base", "base_search_id": bs, "base_search_metric": field,
        "base_search": spl, "search": spl, "search_alert": spl,
        "is_entity_breakdown": True, "is_split_by_entity": True,
        "is_service_entity_filter": True, "is_filter_entities_to_service": True,
        "entity_breakdown_id_fields": split, "entity_id_fields": split,
        "entity_alias_filtering_fields": split, "entity_filter_field": split, "entity_split_field": split,
        "threshold_field": field,
        "aggregate_thresholds": thresholds(field, levels, gmax),
        "entity_thresholds": thresholds(field, levels, gmax),
        "aggregate_statop": agg, "entity_statop": ent,
        "fill_gaps": "null_value", "gap_severity": "info", "gap_severity_value": "1",
        "gap_severity_color": "#AED3E5", "gap_severity_color_light": "#E3F0F6",
        "enabled": True, "alert_period": "5", "alert_lag": lag(bs), "alert_on": "both",
        "urgency": float(importance),
        "type": "kpis_primary", "sec_grp": ip.TEAM, "kpi_threshold_template_id": "",
        "anomaly_detection_alerting_enabled": False, "search_buckets": "", "search_occurrences": 1.0,
        "search_alert_earliest": "5",
    }


def entity_rules(comp: dict) -> list:
    alias = "host_name" if is_agent(comp) else comp["id"]
    return [{"rule_condition": "AND", "rule_items": [
        {"field": alias, "field_type": "alias", "rule_type": "matches", "value": "*"},
        {"field": "etype", "field_type": "info", "rule_type": "matches", "value": comp["etype"]},
    ]}]


def svc_description(comp: dict) -> str:
    return f"Every {comp['etype']} entity. Create narrower services from the {template_title(comp)} template."


def ensure_service_shell(comp: dict):
    """Templates are created from a service, and template KPIs that filter on service entities fail to sync
    to a service without entities, so the service and its entities must exist first."""
    if not ip.get(f"{ip.ITOA}/service/{svc_key(comp)}"):
        ip.post(f"{ip.ITOA}/service", {"_key": svc_key(comp), "title": comp["svc"], "identifying_name": comp["svc"].lower(),
                                        "enabled": False, "entity_rules": entity_rules(comp), "kpis": [], "team_id": ip.TEAM})
        print(f"  created empty service {comp['svc']}")


def upsert_template_and_service(comp: dict):
    spath = f"{ip.ITOA}/service/{svc_key(comp)}"
    title = template_title(comp)
    payload = {
        "_key": template_key(comp), "title": title, "identifying_name": title.lower(),
        "description": f"Infrastructure Monitoring KPIs for {comp['etype']} entities. Linked services receive changes made here.",
        "service_id": svc_key(comp), "kpis": [make_kpi(comp, s) for s in kpi_specs(comp)],
        "team_id": ip.TEAM, "is_healthscore_calculate_by_entity_enabled": 1,
    }
    svc = {"_key": svc_key(comp), "title": comp["svc"], "identifying_name": comp["svc"].lower(), "enabled": True,
           "description": svc_description(comp), "entity_rules": entity_rules(comp), "team_id": ip.TEAM,
           "is_healthscore_calculate_by_entity_enabled": 1}
    current = ip.get(spath)
    linked = current.get("base_service_template_id") == template_key(comp)
    # A template update queues a sync that writes back the linked service as read before the sync, so patch first.
    if linked:
        ip.patch(spath, svc)

    tpath = f"{ip.ITOA}/base_service_template/{template_key(comp)}"
    existing = ip.get(tpath)
    if not existing:
        ip.post(f"{ip.ITOA}/base_service_template", payload)
        existing = ip.get(tpath)
    # Creation copies KPIs and entity rules from the source service and ignores the payload's.
    ip.post(tpath, {**existing, **payload, "entity_rules": entity_rules(comp)})
    print(f"  template {title}: {len(ip.get(tpath).get('kpis', []))} KPIs")

    if linked:
        print(f"  patched service {comp['svc']} (linked)")
        return
    current = ip.get(spath)
    ip.post(spath, {**current, **svc,
                    "kpis": [k for k in current.get("kpis") or [] if str(k.get("_key", "")).startswith("SHKPI-")]})
    ip.post(f"{spath}/base_service_template", {"_key": template_key(comp), "overwrite_entity_rules": "ignore"})
    print(f"  linked service {comp['svc']} to {title}")


def upsert_rollup():
    deps = [{"serviceid": svc_key(c), "kpis_depending_on": [f"SHKPI-{svc_key(c)}"], "overloaded_urgencies": {}}
            for c in COMPONENTS]
    payload = {"_key": ROLLUP["key"], "title": ROLLUP["title"], "identifying_name": ROLLUP["title"].lower(),
               "description": ROLLUP["description"], "enabled": True, "entity_rules": [], "team_id": ip.TEAM}
    path = f"{ip.ITOA}/service/{ROLLUP['key']}"
    if ip.get(path):
        ip.patch(path, payload)
    else:
        ip.post(f"{ip.ITOA}/service", {**payload, "kpis": []})
    ip.patch(path, {"services_depends_on": deps})
    print(f"  rollup {ROLLUP['title']} -> {len(deps)} services")


def run_discovery(comp: dict):
    ip.run_imports({"app": APP}, {"import_name": import_name(comp)})


def bind_entities(comp: dict):
    flt = ip.q(json.dumps({"entity_type_ids": etype_key(comp)}))
    items = ip.get(f"{ip.ITOA}/entity?filter={flt}&fields=_key&count=5000") or []
    keys = [i["_key"] for i in items if i.get("_key")]
    if keys:
        ip.patch(f"{ip.ITOA}/service/{svc_key(comp)}", {"entity_keys": keys})
    print(f"  {comp['svc']}: {len(keys)} entities")


def print_spl():
    for name, (definition, _) in MACROS.items():
        print(f"[macro {name}]\n{definition}\n")
    for key, bs in BASE_SEARCHES.items():
        print(f"[base search {key}]\n{bs['spl']}\n")
    for comp in COMPONENTS:
        print(f"[discovery {import_name(comp)}]\n{discovery_spl(comp)}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--print-spl", action="store_true", help="print macros and searches, then exit")
    ap.add_argument("--host-format", choices=sorted(HOST_FORMATS), help="set the host name macro")
    ap.add_argument("--reset-macros", action="store_true", help="overwrite existing macros with the defaults")
    ap.add_argument("--only", help="comma-separated component keys to deploy (default: all)")
    args = ap.parse_args()
    if args.print_spl:
        print_spl()
        return
    comps = [c for c in COMPONENTS if not args.only or c["key"] in args.only.split(",")]
    print(f"=== {APP_LABEL} ===")
    print("--- macros")
    ensure_macros(args.reset_macros, args.host_format)
    print("--- base searches")
    upsert_base_searches()
    print("--- entity types, dashboards, discovery searches")
    for comp in comps:
        upsert_dashboard(comp)
        upsert_entity_type(comp)
        upsert_discovery(comp, 11 + 4 * COMPONENTS.index(comp))
    print("--- entity discovery")
    for comp in comps:
        ensure_service_shell(comp)
        run_discovery(comp)
    time.sleep(20)
    for comp in comps:
        bind_entities(comp)
    print("--- service templates and services")
    for comp in comps:
        upsert_template_and_service(comp)
    print("--- rollup")
    upsert_rollup()
    time.sleep(30)
    for comp in comps:
        t = ip.get(f"{ip.ITOA}/base_service_template/{template_key(comp)}")
        print(f"  {template_title(comp)}: {t.get('sync_status')} {t.get('last_sync_error') or ''}")
    print("=== done ===")


if __name__ == "__main__":
    main()
