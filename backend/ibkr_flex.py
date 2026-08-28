"""
Client for IBKR's Flex Web Service.

Two-step async flow:
  1. SendRequest(token, query_id) -> a reference code (report is being generated)
  2. GetStatement(token, reference_code) -> poll until the report is ready,
     then it returns the actual Flex Query XML.

Docs: https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/
No local software is required -- these are plain HTTPS calls, so this client
works fine from a server that never touches your own machine.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

SEND_REQUEST_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
DEFAULT_GET_STATEMENT_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"

# IBKR rate-limits Flex Web Service calls; be polite between polls.
POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 12  # ~1 minute of polling before giving up

# Statement-not-ready-yet error code from IBKR's Flex service.
STATEMENT_GENERATION_IN_PROGRESS = "1019"


class FlexServiceError(RuntimeError):
    """Raised when IBKR's Flex Web Service returns an error we can't recover from."""


@dataclass
class FlexReport:
    raw_xml: str


def _request(url: str, params: dict) -> ET.Element:
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    try:
        return ET.fromstring(resp.text)
    except ET.ParseError as exc:
        raise FlexServiceError(f"Could not parse response from {url}: {exc}\nBody: {resp.text[:500]}") from exc


def _send_request(token: str, query_id: str) -> tuple[str, str]:
    """Kick off report generation. Returns (reference_code, get_statement_url)."""
    root = _request(SEND_REQUEST_URL, {"t": token, "q": query_id, "v": "3"})

    status = root.findtext("Status", default="")
    if status != "Success":
        code = root.findtext("ErrorCode", default="?")
        message = root.findtext("ErrorMessage", default="Unknown error")
        raise FlexServiceError(f"SendRequest failed ({code}): {message}")

    reference_code = root.findtext("ReferenceCode", default="")
    get_statement_url = root.findtext("Url", default=DEFAULT_GET_STATEMENT_URL)
    if not reference_code:
        raise FlexServiceError("SendRequest succeeded but returned no ReferenceCode")
    return reference_code, get_statement_url


def _get_statement(token: str, reference_code: str, get_statement_url: str) -> str | None:
    """
    Try to fetch the finished report. Returns the raw XML string once ready,
    or None if IBKR is still generating it (caller should retry).
    """
    resp = requests.get(
        get_statement_url,
        params={"t": token, "q": reference_code, "v": "3"},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.text

    # A finished report starts with <FlexQueryResponse ...>. A "still working"
    # or error response comes back as <FlexStatementResponse>...</FlexStatementResponse>.
    if body.lstrip().startswith("<FlexQueryResponse"):
        return body

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise FlexServiceError(f"Could not parse GetStatement response: {exc}\nBody: {body[:500]}") from exc

    status = root.findtext("Status", default="")
    code = root.findtext("ErrorCode", default="")
    message = root.findtext("ErrorMessage", default="")

    if status == "Success":
        # Shouldn't normally happen (a Success status with no report body),
        # but treat it as "not ready" rather than crash.
        return None
    if code == STATEMENT_GENERATION_IN_PROGRESS:
        return None
    raise FlexServiceError(f"GetStatement failed ({code}): {message}")


def fetch_flex_report(token: str, query_id: str) -> FlexReport:
    """Run the full SendRequest -> poll GetStatement flow and return the raw XML."""
    if not token or not query_id:
        raise FlexServiceError("IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID must both be set")

    reference_code, get_statement_url = _send_request(token, query_id)

    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        xml_body = _get_statement(token, reference_code, get_statement_url)
        if xml_body is not None:
            return FlexReport(raw_xml=xml_body)
        time.sleep(POLL_INTERVAL_SECONDS)

    raise FlexServiceError(
        f"Report never finished generating after {MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS}s. "
        "IBKR can be slow during market hours -- try again shortly."
    )


def validate_flex_credentials(token: str, query_id: str) -> None:
    """Validate credentials by starting report generation, without polling it."""
    if not token or not query_id:
        raise FlexServiceError("Flex token and query ID must both be set")
    _send_request(token, query_id)
