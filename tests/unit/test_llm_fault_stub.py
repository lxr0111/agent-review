from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from scripts.llm_fault_stub import FaultController, FaultStubHandler


@contextmanager
def _fault_server(controller: FaultController) -> Iterator[str]:
    class Handler(FaultStubHandler):
        pass

    Handler.controller = controller
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("scenario", ["timeout", "rate_limit", "server_error"])
def test_retryable_fault_recovers_after_configured_failures(scenario: str) -> None:
    controller = FaultController(scenario=scenario, failures_before_success=1)

    first = controller.next_decision()
    second = controller.next_decision()
    snapshot = controller.snapshot()

    assert first.action == scenario
    assert second.action == "success"
    assert snapshot.request_count == 2
    assert snapshot.failure_count == 1
    assert snapshot.success_count == 1


def test_retryable_fault_can_demonstrate_exponential_backoff() -> None:
    controller = FaultController(scenario="server_error", failures_before_success=2)

    assert controller.next_decision().action == "server_error"
    assert controller.next_decision().action == "server_error"
    assert controller.next_decision().action == "success"

    snapshot = controller.snapshot()
    assert snapshot.request_count == 3
    assert snapshot.failure_count == 2
    assert snapshot.success_count == 1


@pytest.mark.parametrize("scenario", ["unauthorized", "forbidden", "invalid_schema"])
def test_permanent_fault_does_not_recover_implicitly(scenario: str) -> None:
    controller = FaultController(scenario=scenario)

    assert controller.next_decision().action == scenario
    assert controller.next_decision().action == scenario


def test_configure_resets_counters() -> None:
    controller = FaultController(scenario="server_error")
    controller.next_decision()

    snapshot = controller.configure(scenario="success")

    assert snapshot.scenario == "success"
    assert snapshot.request_count == 0
    assert snapshot.failure_count == 0
    assert snapshot.success_count == 0


def test_controller_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        FaultController(scenario="unknown")

    with pytest.raises(ValueError, match="500 or 503"):
        FaultController(scenario="server_error", server_error_status=502)


def test_http_stub_configures_fault_and_recovers() -> None:
    controller = FaultController()
    with _fault_server(controller) as base_url, httpx.Client(base_url=base_url) as client:
        configured = client.post(
            "/__fault__/configure",
            json={
                "scenario": "rate_limit",
                "failures_before_success": 1,
                "retry_after_seconds": 2,
            },
        )
        first = client.post("/responses", json={"input": "ignored"})
        second = client.post("/responses", json={"input": "ignored"})
        state = client.get("/__fault__/state")

    assert configured.status_code == 200
    assert first.status_code == 429
    assert first.headers["retry-after"] == "2"
    assert second.status_code == 200
    assert second.json()["status"] == "completed"
    assert state.json()["request_count"] == 2
