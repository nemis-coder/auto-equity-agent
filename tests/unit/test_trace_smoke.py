from types import SimpleNamespace
from unittest.mock import Mock

from scripts.trace_smoke import verify_receipt


def client_with(*results):
    get = Mock(side_effect=results)
    return SimpleNamespace(api=SimpleNamespace(observations=SimpleNamespace(get_many=get)))


def test_receipt_requires_both_observations():
    partial = SimpleNamespace(data=[SimpleNamespace(name="smoke.turn")])
    full = SimpleNamespace(data=[SimpleNamespace(name=n)
                                       for n in ("smoke.turn", "smoke.tool")])
    client = client_with(RuntimeError("pending"), partial, full)
    assert verify_receipt(client, "a" * 32, attempts=3, delay=0)
    assert client.api.observations.get_many.call_count == 3
    params = client.api.observations.get_many.call_args.kwargs
    assert params["trace_id"] == "a" * 32 and params["fields"] == "core,basic"
    assert params["from_start_time"] < params["to_start_time"]


def test_export_or_read_failure_never_claims_receipt():
    client = client_with(RuntimeError("offline"), RuntimeError("offline"))
    assert not verify_receipt(client, "a" * 32, attempts=2, delay=0)
