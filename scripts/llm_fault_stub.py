"""Test-only Responses API stub for deterministic worker retry drills."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Any

RETRYABLE_SCENARIOS = frozenset({"timeout", "rate_limit", "server_error"})
PERMANENT_SCENARIOS = frozenset({"unauthorized", "forbidden", "invalid_schema"})
SUCCESS_SCENARIOS = frozenset({"success", "finding_without_location"})
SUPPORTED_SCENARIOS = frozenset(
    {*SUCCESS_SCENARIOS, *RETRYABLE_SCENARIOS, *PERMANENT_SCENARIOS}
)


@dataclass(frozen=True, slots=True)
class FaultDecision:
    action: str
    request_number: int
    timeout_seconds: float
    retry_after_seconds: int
    server_error_status: int


@dataclass(frozen=True, slots=True)
class FaultSnapshot:
    scenario: str
    failures_before_success: int
    timeout_seconds: float
    retry_after_seconds: int
    server_error_status: int
    request_count: int
    failure_count: int
    success_count: int


class FaultController:
    def __init__(
        self,
        *,
        scenario: str = "success",
        failures_before_success: int = 1,
        timeout_seconds: float = 2.0,
        retry_after_seconds: int = 2,
        server_error_status: int = 503,
    ) -> None:
        self._lock = Lock()
        self.configure(
            scenario=scenario,
            failures_before_success=failures_before_success,
            timeout_seconds=timeout_seconds,
            retry_after_seconds=retry_after_seconds,
            server_error_status=server_error_status,
        )

    def configure(
        self,
        *,
        scenario: str,
        failures_before_success: int = 1,
        timeout_seconds: float = 2.0,
        retry_after_seconds: int = 2,
        server_error_status: int = 503,
    ) -> FaultSnapshot:
        if scenario not in SUPPORTED_SCENARIOS:
            raise ValueError(f"Unsupported fault scenario: {scenario}")
        if not 0 <= failures_before_success <= 10:
            raise ValueError("failures_before_success must be between 0 and 10")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be greater than 0 and at most 60")
        if not 0 <= retry_after_seconds <= 3_600:
            raise ValueError("retry_after_seconds must be between 0 and 3600")
        if server_error_status not in {500, 503}:
            raise ValueError("server_error_status must be 500 or 503")

        with self._lock:
            self._scenario = scenario
            self._failures_before_success = failures_before_success
            self._timeout_seconds = timeout_seconds
            self._retry_after_seconds = retry_after_seconds
            self._server_error_status = server_error_status
            self._request_count = 0
            self._failure_count = 0
            self._success_count = 0
            return self._snapshot_unlocked()

    def next_decision(self) -> FaultDecision:
        with self._lock:
            self._request_count += 1
            should_recover = (
                self._scenario in RETRYABLE_SCENARIOS
                and self._request_count > self._failures_before_success
            )
            action = "success" if should_recover else self._scenario
            if action in SUCCESS_SCENARIOS:
                self._success_count += 1
            else:
                self._failure_count += 1
            return FaultDecision(
                action=action,
                request_number=self._request_count,
                timeout_seconds=self._timeout_seconds,
                retry_after_seconds=self._retry_after_seconds,
                server_error_status=self._server_error_status,
            )

    def snapshot(self) -> FaultSnapshot:
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> FaultSnapshot:
        return FaultSnapshot(
            scenario=self._scenario,
            failures_before_success=self._failures_before_success,
            timeout_seconds=self._timeout_seconds,
            retry_after_seconds=self._retry_after_seconds,
            server_error_status=self._server_error_status,
            request_count=self._request_count,
            failure_count=self._failure_count,
            success_count=self._success_count,
        )


class FaultStubHandler(BaseHTTPRequestHandler):
    controller: FaultController
    server_version = "AgentReviewFaultStub/1.0"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/__fault__/state":
            self._send_json(HTTPStatus.OK, asdict(self.controller.snapshot()))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/__fault__/configure":
            self._configure()
            return
        if self.path == "/responses":
            self._discard_request_body()
            self._respond_to_model_request()
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _configure(self) -> None:
        try:
            payload = self._read_json_body()
            snapshot = self.controller.configure(
                scenario=_required_string(payload, "scenario"),
                failures_before_success=_optional_int(payload, "failures_before_success", 1),
                timeout_seconds=_optional_float(payload, "timeout_seconds", 2.0),
                retry_after_seconds=_optional_int(payload, "retry_after_seconds", 2),
                server_error_status=_optional_int(payload, "server_error_status", 503),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._send_json(HTTPStatus.OK, asdict(snapshot))

    def _respond_to_model_request(self) -> None:
        decision = self.controller.next_decision()
        print(
            json.dumps(
                {
                    "event": "fault_stub_request",
                    "action": decision.action,
                    "request_number": decision.request_number,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )

        if decision.action == "timeout":
            time.sleep(decision.timeout_seconds)
            self._send_json(
                HTTPStatus.GATEWAY_TIMEOUT,
                {"error": {"message": "intentional timeout fault"}},
            )
            return
        if decision.action == "rate_limit":
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": {"message": "intentional rate-limit fault"}},
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
            return
        if decision.action == "server_error":
            self._send_json(
                decision.server_error_status,
                {"error": {"message": "intentional server fault"}},
            )
            return
        if decision.action == "unauthorized":
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": {"message": "intentional authentication fault"}},
            )
            return
        if decision.action == "forbidden":
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": {"message": "intentional permission fault"}},
            )
            return
        if decision.action == "invalid_schema":
            self._send_json(HTTPStatus.OK, _response_payload('{"summary":"missing findings"}'))
            return
        if decision.action == "finding_without_location":
            self._send_json(
                HTTPStatus.OK,
                _response_payload(
                    json.dumps(
                        {
                            "summary": "A deterministic finding has no safe inline location.",
                            "findings": [
                                {
                                    "title": "Deterministic summary-only finding",
                                    "explanation": (
                                        "This test-only finding verifies that review content remains "
                                        "visible when no validated line range is available."
                                    ),
                                    "suggestion": (
                                        "Keep the finding in the review summary without creating an "
                                        "inline comment."
                                    ),
                                    "file_path": "demo.py",
                                    "severity": "medium",
                                    "category": "correctness",
                                    "confidence": 0.95,
                                    "location": None,
                                }
                            ],
                        },
                        separators=(",", ":"),
                    )
                ),
            )
            return
        self._send_json(
            HTTPStatus.OK,
            _response_payload('{"summary":"Fault injection review completed.","findings":[]}'),
        )

    def _read_json_body(self) -> dict[str, Any]:
        content_length = _content_length(self.headers.get("Content-Length"))
        if content_length > 4_096:
            raise ValueError("request body is too large")
        payload = json.loads(self.rfile.read(content_length) or b"{}")
        if not isinstance(payload, dict):
            raise TypeError("request body must be a JSON object")
        return payload

    def _discard_request_body(self) -> None:
        content_length = _content_length(self.headers.get("Content-Length"))
        if content_length:
            self.rfile.read(content_length)

    def _send_json(
        self,
        status: int,
        payload: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Request-Id", "fault-stub-request")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def _response_payload(output_text: str) -> dict[str, object]:
    return {
        "id": "resp_fault_stub",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": output_text}],
            }
        ],
    }


def _content_length(value: str | None) -> int:
    if value is None:
        return 0
    length = int(value)
    if length < 0 or length > 2_000_000:
        raise ValueError("invalid Content-Length")
    return length


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    if not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer")
    return value


def _optional_float(payload: dict[str, Any], key: str, default: float) -> float:
    value = payload.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{key} must be a number")
    return float(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("FAULT_STUB_PORT", "8090")))
    args = parser.parse_args()

    controller = FaultController(
        timeout_seconds=float(os.getenv("FAULT_TIMEOUT_SECONDS", "2")),
        retry_after_seconds=int(os.getenv("FAULT_RETRY_AFTER_SECONDS", "2")),
    )
    FaultStubHandler.controller = controller
    server = ThreadingHTTPServer((args.host, args.port), FaultStubHandler)
    print(
        json.dumps(
            {"event": "fault_stub_started", "host": args.host, "port": args.port},
            separators=(",", ":"),
        ),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
