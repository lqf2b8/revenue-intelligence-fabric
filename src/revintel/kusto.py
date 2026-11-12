"""Talk to any Kusto endpoint: the local emulator, the free Azure Data Explorer
cluster, or a Fabric Eventhouse. Configured entirely through environment variables.

KUSTO_URL           query endpoint, e.g. http://kusto:8080 or https://<cluster>.kusto.windows.net
KUSTO_DATABASE      database name (emulator default: NetDefaultDB)
KUSTO_AUTH          none (emulator) | azcli | app
KUSTO_CLIENT_ID / KUSTO_CLIENT_SECRET / KUSTO_TENANT_ID   for KUSTO_AUTH=app
KUSTO_INGEST_MODE   command (emulator: `.ingest` from a file the cluster can read)
                    | queued (cloud: upload through the ingestion endpoint)
KUSTO_INGEST_URL    ingestion endpoint for queued mode (https://ingest-<cluster>...)
KUSTO_FILE_ROOT     where the emulator sees the shared data folder (default /kustodata)
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class KustoSettings:
    url: str
    database: str
    auth: str = "none"
    client_id: str | None = None
    client_secret: str | None = None
    tenant_id: str | None = None
    ingest_mode: str = "command"
    ingest_url: str | None = None
    file_root: str = "/kustodata"

    @classmethod
    def from_env(cls) -> "KustoSettings":
        e = os.environ
        return cls(
            url=e.get("KUSTO_URL", "http://localhost:8080"),
            database=e.get("KUSTO_DATABASE", "NetDefaultDB"),
            auth=e.get("KUSTO_AUTH", "none"),
            client_id=e.get("KUSTO_CLIENT_ID"),
            client_secret=e.get("KUSTO_CLIENT_SECRET"),
            tenant_id=e.get("KUSTO_TENANT_ID"),
            ingest_mode=e.get("KUSTO_INGEST_MODE", "command"),
            ingest_url=e.get("KUSTO_INGEST_URL"),
            file_root=e.get("KUSTO_FILE_ROOT", "/kustodata"),
        )


def _kcsb(url: str, s: KustoSettings):
    from azure.kusto.data import KustoConnectionStringBuilder as K

    if s.auth == "none":
        return K.with_no_authentication(url)
    if s.auth == "azcli":
        return K.with_az_cli_authentication(url)
    if s.auth == "app":
        return K.with_aad_application_key_authentication(url, s.client_id, s.client_secret, s.tenant_id)
    raise ValueError(f"KUSTO_AUTH must be none, azcli or app, not {s.auth!r}")


def client(s: KustoSettings):
    from azure.kusto.data import KustoClient

    return KustoClient(_kcsb(s.url, s))


def wait_until_ready(s: KustoSettings, timeout_s: float = 300, every_s: float = 5) -> None:
    """The emulator takes a minute or so to start; poll until `.show version` answers."""
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client(s).execute_mgmt(s.database, ".show version")
            return
        except Exception as e:  # noqa: BLE001 - any failure means "not yet"
            last = e
            time.sleep(every_s)
    raise TimeoutError(f"Kusto at {s.url} not ready after {timeout_s}s: {last}")


# ---------- scripts ----------

_CMD_START = re.compile(r"^\.(create|alter|drop|set|append|ingest|show|execute|delete|clear)", re.IGNORECASE)


def split_commands(text: str) -> list[str]:
    """Split a .kql file into control commands.

    A command starts at a line beginning with `.create`, `.alter`, ... and runs to
    the next such line. `//` comment lines are dropped, except inside ``` blocks
    (the multi-line string syntax used for ingestion mappings). Lines before the
    first command, and plain queries, are ignored.
    """
    cmds: list[list[str]] = []
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if not in_block and stripped.startswith("//"):
            continue
        if not in_block and _CMD_START.match(line):
            cmds.append([line])
        elif cmds:
            cmds[-1].append(line)
        if stripped.startswith("```") and cmds:
            in_block = not in_block
    return [c for c in ("\n".join(lines).strip() for lines in cmds) if c]


def apply_script(s: KustoSettings, path: Path) -> list[str]:
    c = client(s)
    applied = []
    for cmd in split_commands(path.read_text()):
        c.execute_mgmt(s.database, cmd)
        applied.append(cmd.splitlines()[0][:90])
    return applied


# ---------- queries ----------

def query_df(s: KustoSettings, query: str) -> pd.DataFrame:
    from azure.kusto.data.helpers import dataframe_from_result_table

    res = client(s).execute_query(s.database, query)
    return dataframe_from_result_table(res.primary_results[0])


def scalar(s: KustoSettings, query: str):
    df = query_df(s, query)
    return df.iat[0, 0]


# ---------- ingestion ----------

def ingest_csv(s: KustoSettings, table: str, local_path: Path, mapping: str | None = None) -> None:
    """Ingest a CSV(.gz) with a header row.

    command mode: the cluster reads the file itself, so local_path must sit in the
    folder shared with the emulator; its path inside the emulator is
    KUSTO_FILE_ROOT/<file name>.
    queued mode: the file is uploaded through the ingestion endpoint.
    """
    if s.ingest_mode == "command":
        remote = f"{s.file_root.rstrip('/')}/{local_path.name}"
        props = ["format='csv'", "ignoreFirstRecord=true"]
        if mapping:
            props.append(f"ingestionMappingReference='{mapping}'")
        client(s).execute_mgmt(s.database, f".ingest into table {table} (@'{remote}') with ({', '.join(props)})")
        return
    if s.ingest_mode == "queued":
        from azure.kusto.data.data_format import DataFormat
        from azure.kusto.ingest import IngestionProperties, QueuedIngestClient

        if not s.ingest_url:
            raise ValueError("KUSTO_INGEST_URL is required for queued ingestion")
        props = IngestionProperties(database=s.database, table=table, data_format=DataFormat.CSV,
                                    ignore_first_record=True, ingestion_mapping_reference=mapping)
        QueuedIngestClient(_kcsb(s.ingest_url, s)).ingest_from_file(str(local_path), ingestion_properties=props)
        return
    raise ValueError(f"KUSTO_INGEST_MODE must be command or queued, not {s.ingest_mode!r}")


def load_usage(s: KustoSettings, usage_file: Path, table: str = "UsageEvents") -> int:
    """Replace the table's rows with the usage file, then check the row count. Safe to re-run."""
    expected = len(pd.read_csv(usage_file, usecols=["Day"]))
    client(s).execute_mgmt(s.database, f".clear table {table} data")
    ingest_csv(s, table, usage_file, mapping="UsageEventsCsv")
    if s.ingest_mode == "queued":
        return expected  # queued ingestion lands asynchronously; count later with `| count`
    got = int(scalar(s, f"{table} | count"))
    if got != expected:
        raise RuntimeError(f"{table}: expected {expected:,} rows after ingest, found {got:,}")
    return got
