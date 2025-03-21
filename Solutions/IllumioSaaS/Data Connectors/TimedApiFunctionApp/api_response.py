import logging
import requests
from base64 import b64encode
import azure.functions as func
import polars as pl
import json
import aiohttp
from ..CommonCode.sentinel_connector import AzureSentinelConnectorAsync
from ..CommonCode.constants import (
    API_KEY,
    API_SECRET,
    PCE_FQDN,
    PORT,
    ORG_ID,
    AZURE_CLIENT_ID,
    AZURE_CLIENT_SECRET,
    AZURE_TENANT_ID,
    DCE_ENDPOINT,
    DCR_ID,
    WORKLOADS_API_LOGS_CUSTOM_TABLE,
    MAX_WORKLOADS,
    ONPREM_API_KEY,
    ONPREM_API_SECRET,
    ONPREM_PCE_FQDN,
    ONPREM_PCE_PORT,
    ONPREM_PCE_ORGID,
)


def getVensByVersion(data):
    try:
        filtered_data = data.filter(pl.col("managed") == True)
        grouped_data = (
            filtered_data.group_by("ven.version").agg(pl.len()).rename({"len": "size"})
        )
        return dict(zip(grouped_data["ven.version"], grouped_data["size"]))
    except Exception as e:
        # You can log the exception here if needed
        logging.error("getVensByVersion error: {e}")
        return {}


def getVensByManaged(data):
    try:
        grouped_data = data.group_by("managed").agg(pl.len()).rename({"len": "size"})
        # Convert to dictionary
        return dict(zip(grouped_data["managed"], grouped_data["size"]))
    except Exception as e:
        logging.error("getVensByManaged error: {e}")
        return {}


def getVensByType(data):
    try:
        filtered_data = data.filter(pl.col("managed") == True)
        results = (
            filtered_data.group_by("ven.ven_type").agg(pl.len()).rename({"len": "size"})
        )
        return dict(zip(results["ven.ven_type"], results["size"]))
    except Exception as e:
        logging.error("getVensByType error: {e}")
        return {}


def getVensByOS(data):
    try:
        filtered_data = data.filter(pl.col("managed") == True)
        results = filtered_data.group_by("os_id").agg(pl.len()).rename({"len": "size"})
        return dict(zip(results["os_id"], results["size"]))
    except Exception as e:
        logging.error("getVensByOS error: {e}")
        return {}


def getVensByEnforcementMode(data):
    try:
        filtered_data = data.filter(pl.col("managed") == True)
        results = (
            filtered_data.group_by("enforcement_mode")
            .agg(pl.len())
            .rename({"len": "size"})
        )
        return dict(zip(results["enforcement_mode"], results["size"]))
    except Exception as e:
        logging.error("getVensByEnforcementMode error: {e}")
        return {}


def getVensByStatus(data):
    try:
        filtered_data = data.filter(pl.col("managed") == True)
        results = (
            filtered_data.group_by("ven.status").agg(pl.len()).rename({"len": "size"})
        )
        return dict(zip(results["ven.status"], results["size"]))
    except Exception as e:
        logging.error("getVensByStatus error: {e}")
        return {}


def getVensBySyncState(data):
    try:
        filtered_data = data.filter(pl.col("managed") == True)
        results = (
            filtered_data.group_by("agent.status.security_policy_sync_state")
            .agg(pl.len())
            .rename({"len": "size"})
        )
        return dict(
            zip(results["agent.status.security_policy_sync_state"], results["size"])
        )
    except Exception as e:
        logging.error("getVensBySyncState error: {e}")
        return {}


# Return list of pce fqdns for which workloads api call is executed
# If onprem pce and saas pce are configured to send events, then this method
# should return 2 entries else return only saas
def getPceType():
    if ONPREM_PCE_FQDN:
        return ["onprem", "saas"]
    return ["saas"]


def getWorkloads(pce_fqdn, port, org, api_key, api_secret):
    if pce_fqdn:
        url = "https://{}:{}/api/v2/orgs/{}/workloads/?max_results={}".format(
            pce_fqdn, port, org, MAX_WORKLOADS
        )

        logging.debug("url to be exercised is {} ".format(url))

        credentials = b64encode(f"{api_key}:{api_secret}".encode()).decode("utf-8")
        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-type": "application/json",
        }

        return requests.request("GET", url, headers=headers, data={})


async def main(mytimer: func.TimerRequest) -> None:

    for pce_type in getPceType():
        pce_fqdn = None
        if pce_type == "onprem":
            pce_fqdn = ONPREM_PCE_FQDN
            response = getWorkloads(
                ONPREM_PCE_FQDN,
                ONPREM_PCE_PORT,
                ONPREM_PCE_ORGID,
                ONPREM_API_KEY,
                ONPREM_API_SECRET,
            )
        else:
            pce_fqdn = PCE_FQDN
            response = getWorkloads(
                PCE_FQDN,
                PORT,
                ORG_ID,
                API_KEY,
                API_SECRET,
            )

        if response:
            logging.info("[TimedApi] Response from url is {}".format(response.headers))
        else:
            logging.info("[TimedApi] Error in response {}".format(response))
            continue

        response = json.loads(response.text)
        df = pl.json_normalize(response, infer_schema_length=None)

        vens_by_version = getVensByVersion(df)
        vens_by_managed = getVensByManaged(df)
        vens_by_type = getVensByType(df)
        vens_by_os = getVensByOS(df)
        vens_by_enf_mode = getVensByEnforcementMode(df)
        vens_by_status = getVensByStatus(df)
        vens_by_sync_state = getVensBySyncState(df)
        api_response = []
        api_response.append(
            {
                "vens_by_version": vens_by_version,
                "vens_by_managed": vens_by_managed,
                "vens_by_type": vens_by_type,
                "vens_by_os": vens_by_os,
                "vens_by_enforcement_mode": vens_by_enf_mode,
                "vens_by_status": vens_by_status,
                "vens_by_sync_state": vens_by_sync_state,
                "pce_fqdn": pce_fqdn,
            }
        )

        logging.info(
            "[TimedApi] Summary of workload api response that will be stored in log analytics table is {}".format(
                api_response
            )
        )

        async with aiohttp.ClientSession() as session:
            sentinel = AzureSentinelConnectorAsync(
                session,
                DCE_ENDPOINT,
                DCR_ID,
                WORKLOADS_API_LOGS_CUSTOM_TABLE,
                AZURE_CLIENT_ID,
                AZURE_CLIENT_SECRET,
                AZURE_TENANT_ID,
                queue_size=1,
            )
            await sentinel.send(api_response)
