"""BMCClient - Redfish HTTP client with retry and exponential backoff."""

import base64
import json
import ssl
import time
from urllib import request, error


class BMCError(Exception):
    """Base exception for BMC operations."""
    pass


class BMCConnectionError(BMCError):
    """Connection-level failures (timeout, refused, DNS)."""
    pass


class BMCAuthError(BMCError):
    """Authentication failures (401, 403)."""
    pass


class BMCHTTPError(BMCError):
    """HTTP-level errors with status code."""

    def __init__(self, message, status_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class BMCClient:
    """Redfish API client for a single BMC endpoint.

    Uses standard library only (urllib, ssl, base64, json).
    Includes retry with exponential backoff for transient errors.
    """

    # HTTP status codes that are retryable (server-side transient)
    RETRYABLE_STATUS = {500, 502, 503, 504}
    # HTTP status codes that should never be retried
    NO_RETRY_STATUS = {400, 401, 403, 404}

    def __init__(self, host, username, password, max_retries=3, timeout=30):
        self.base_url = f"https://{host}"
        self.host = host
        self.username = username
        self.password = password
        self.max_retries = max_retries
        self.timeout = timeout
        self._auth_header = self._make_auth_header(username, password)
        self._ssl_ctx = self._make_ssl_context()

    @staticmethod
    def _make_auth_header(username, password):
        cred = f"{username}:{password}"
        encoded = base64.b64encode(cred.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    @staticmethod
    def _make_ssl_context():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _request(self, method, path, data=None, headers=None, raw=False):
        """Make an HTTP request with retry/backoff.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            path: URL path (e.g. /redfish/v1/Systems/1)
            data: Dict to send as JSON body, or bytes if raw
            headers: Additional headers dict
            raw: If True, return raw bytes instead of parsed JSON

        Returns:
            Parsed JSON dict, or raw bytes if raw=True

        Raises:
            BMCAuthError: On 401/403
            BMCHTTPError: On non-retryable HTTP errors
            BMCConnectionError: On connection failures after all retries
        """
        url = f"{self.base_url}{path}"
        req_headers = {
            "Authorization": self._auth_header,
        }
        if headers:
            req_headers.update(headers)

        body = None
        if data is not None:
            if isinstance(data, bytes):
                body = data
            else:
                req_headers["Content-Type"] = "application/json"
                body = json.dumps(data).encode("utf-8")

        last_error = None
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                sleep_time = 2 ** attempt  # 2s, 4s, 8s
                time.sleep(sleep_time)

            try:
                req = request.Request(
                    url, headers=req_headers, method=method, data=body
                )
                with request.urlopen(
                    req, context=self._ssl_ctx, timeout=self.timeout
                ) as resp:
                    resp_body = resp.read()
                    if raw:
                        return resp_body
                    if not resp_body:
                        return {"Success": {"Message": f"Action completed with status {resp.getcode()}."}}
                    return json.loads(resp_body.decode("utf-8"))

            except error.HTTPError as e:
                status = e.code
                try:
                    err_body = e.read().decode("utf-8")
                except Exception:
                    err_body = ""

                if status in (401, 403):
                    raise BMCAuthError(
                        f"Authentication failed for {self.host}: HTTP {status}"
                    )
                if status in self.NO_RETRY_STATUS:
                    raise BMCHTTPError(
                        f"HTTP {status} from {self.host}{path}: {err_body}",
                        status_code=status,
                        body=err_body,
                    )
                if status in self.RETRYABLE_STATUS:
                    last_error = BMCHTTPError(
                        f"HTTP {status} from {self.host}{path}",
                        status_code=status,
                        body=err_body,
                    )
                    continue
                # Unknown status - don't retry
                raise BMCHTTPError(
                    f"HTTP {status} from {self.host}{path}: {err_body}",
                    status_code=status,
                    body=err_body,
                )

            except error.URLError as e:
                last_error = BMCConnectionError(
                    f"Connection error to {self.host}: {e.reason}"
                )
                continue

            except Exception as e:
                last_error = BMCConnectionError(
                    f"Unexpected error connecting to {self.host}: {e}"
                )
                continue

        # All retries exhausted
        raise last_error

    def get(self, path):
        """GET a Redfish resource."""
        return self._request("GET", path)

    def post(self, path, data=None):
        """POST to a Redfish resource."""
        return self._request("POST", path, data=data)

    def patch(self, path, data):
        """PATCH a Redfish resource."""
        return self._request("PATCH", path, data=data)

    def delete(self, path):
        """DELETE a Redfish resource."""
        return self._request("DELETE", path)

    def cgi_request(self, path, method="GET", data=None, headers=None):
        """Make a raw CGI request (for screenshots, etc). Returns raw bytes."""
        return self._request(method, path, data=data, headers=headers, raw=True)
