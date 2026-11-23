"""Check that the adopted Harness stops an agent's existing tools from reading memory."""

from unittest.mock import Mock

from octop_harness.builtin.tools.memory_tools import build_memory_tools


def test_memory_read_tools_stop_when_access_is_revoked() -> None:
    note = "private note for this test"
    service = Mock()
    service.get.return_value = {"content": note}
    service.search.return_value = {"hits": [{"path": "atom/test.md", "snippet": note}]}
    access_enabled = True
    tools = {
        tool.name: tool
        for tool in build_memory_tools(service, access_allowed=lambda: access_enabled)
    }
    reads = {
        "memory_get": {"path": "atom/test.md"},
        "memory_search": {"query": "test"},
    }

    for name, args in reads.items():
        assert note in tools[name].invoke(args)

    service.reset_mock()
    access_enabled = False
    for name, args in reads.items():
        result = tools[name].invoke(args)
        assert "unavailable" in result
        assert note not in result
    assert not service.mock_calls
