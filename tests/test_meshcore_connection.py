"""Tests for MeshCore connection setup: serial vs TCP, node configuration
and advert scheduling."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from citadel.transport.engines.meshcore.main import MeshCoreTransportEngine
from citadel.transport.manager import TransportError

MODULE = "citadel.transport.engines.meshcore.main"


def make_engine(mc_config):
    """Build an engine with only what start_meshcore() needs."""
    engine = MeshCoreTransportEngine.__new__(MeshCoreTransportEngine)
    engine.mc_config = mc_config
    engine.config = SimpleNamespace(transport={"meshcore": mc_config})
    engine.scheds = []
    engine.tasks = []
    engine._create_monitored_task = MagicMock(
        side_effect=lambda coro, name: coro.close())
    return engine


def make_mc():
    ok = SimpleNamespace(type=None, payload=None)
    mc = MagicMock()
    for cmd in ("set_time", "set_radio", "set_tx_power", "set_name",
                "set_multi_acks", "send_advert"):
        setattr(mc.commands, cmd, AsyncMock(return_value=ok))
    mc.ensure_contacts = AsyncMock(return_value=True)
    return mc


@pytest.mark.asyncio
async def test_tcp_connection_uses_create_tcp():
    engine = make_engine({"connection": "tcp", "tcp_host": "192.0.2.10",
                          "tcp_port": 5003})
    mc = make_mc()
    with patch(f"{MODULE}.MeshCore.create_tcp",
               AsyncMock(return_value=mc)) as create_tcp, \
            patch(f"{MODULE}.MeshCore.create_serial", AsyncMock()) as create_serial:
        await engine.start_meshcore()

    create_tcp.assert_awaited_once()
    args, kwargs = create_tcp.call_args
    assert args == ("192.0.2.10", 5003)
    assert kwargs["auto_reconnect"] is True
    create_serial.assert_not_awaited()
    assert engine.meshcore is mc


@pytest.mark.asyncio
async def test_serial_is_default():
    engine = make_engine({"serial_port": "/dev/ttyACM0", "baud_rate": 115200})
    mc = make_mc()
    with patch(f"{MODULE}.MeshCore.create_serial",
               AsyncMock(return_value=mc)) as create_serial, \
            patch(f"{MODULE}.MeshCore.create_tcp", AsyncMock()) as create_tcp:
        await engine.start_meshcore()

    assert create_serial.call_args.args == ("/dev/ttyACM0", 115200)
    create_tcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_tcp_without_host_fails():
    engine = make_engine({"connection": "tcp"})
    with pytest.raises(TransportError, match="tcp_host"):
        await engine.start_meshcore()


@pytest.mark.asyncio
async def test_unknown_connection_type_fails():
    engine = make_engine({"connection": "bluetooth"})
    with pytest.raises(TransportError, match="bluetooth"):
        await engine.start_meshcore()


@pytest.mark.asyncio
async def test_tcp_connection_refused_raises_transport_error():
    engine = make_engine({"connection": "tcp", "tcp_host": "192.0.2.10"})
    with patch(f"{MODULE}.MeshCore.create_tcp",
               AsyncMock(side_effect=ConnectionRefusedError("refused"))):
        with pytest.raises(TransportError, match="refused"):
            await engine.start_meshcore()


@pytest.mark.asyncio
async def test_configure_node_off_leaves_node_settings_alone():
    engine = make_engine({"connection": "tcp", "tcp_host": "192.0.2.10",
                          "configure_node": False})
    mc = make_mc()
    with patch(f"{MODULE}.MeshCore.create_tcp", AsyncMock(return_value=mc)):
        await engine.start_meshcore()

    mc.commands.set_time.assert_awaited_once()
    for cmd in ("set_radio", "set_tx_power", "set_name", "set_multi_acks"):
        getattr(mc.commands, cmd).assert_not_awaited()


@pytest.mark.asyncio
async def test_configure_node_defaults_to_on():
    engine = make_engine({"connection": "tcp", "tcp_host": "192.0.2.10",
                          "name": "Test BBS"})
    mc = make_mc()
    with patch(f"{MODULE}.MeshCore.create_tcp", AsyncMock(return_value=mc)):
        await engine.start_meshcore()

    mc.commands.set_radio.assert_awaited_once()
    mc.commands.set_name.assert_awaited_once_with("Test BBS")


@pytest.mark.asyncio
async def test_advert_interval_zero_disables_adverts():
    engine = make_engine({"connection": "tcp", "tcp_host": "192.0.2.10",
                          "advert_interval": 0})
    with patch(f"{MODULE}.MeshCore.create_tcp",
               AsyncMock(return_value=make_mc())):
        await engine.start_meshcore()
    assert engine.scheds == []
    engine._create_monitored_task.assert_not_called()


@pytest.mark.asyncio
async def test_advert_scheduler_started_by_default():
    engine = make_engine({"connection": "tcp", "tcp_host": "192.0.2.10"})
    with patch(f"{MODULE}.MeshCore.create_tcp",
               AsyncMock(return_value=make_mc())):
        await engine.start_meshcore()
    assert len(engine.scheds) == 1


# ------------------------------------------------------------
# ignore_commands
# ------------------------------------------------------------

def make_router(mc_config):
    from citadel.transport.engines.meshcore.message_router import MessageRouter
    config = SimpleNamespace(transport={"meshcore": mc_config})
    dedupe = MagicMock()
    dedupe.is_duplicate = AsyncMock(return_value=False)
    session_mgr = MagicMock()
    session_mgr.get_session_by_node_id = MagicMock(return_value=None)
    router = MessageRouter(config, None, session_mgr, MagicMock(), dedupe,
                           MagicMock(), MagicMock())
    return router, session_mgr, dedupe


def dm(text):
    return SimpleNamespace(payload={"pubkey_prefix": "4242d01aa7a1",
                                    "text": text, "sender_timestamp": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["status", " STATUS ", "Status"])
async def test_ignored_command_is_not_answered(text):
    router, session_mgr, dedupe = make_router(
        {"ignore_commands": ["status"]})
    await router._process_mc_message_safe(dm(text))
    session_mgr.create_session.assert_not_called()
    dedupe.is_duplicate.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_messages_are_still_handled():
    router, session_mgr, dedupe = make_router(
        {"ignore_commands": ["status"]})
    router._start_bbs_listener_func = AsyncMock()
    # stop processing right after session creation
    router.node_auth.node_has_password_cache = AsyncMock(
        side_effect=RuntimeError("stop"))
    await router._process_mc_message_safe(dm("status please"))
    dedupe.is_duplicate.assert_awaited_once()
    session_mgr.create_session.assert_called_once_with("4242d01aa7a1")


@pytest.mark.asyncio
async def test_no_ignore_commands_by_default():
    router, session_mgr, dedupe = make_router({})
    router._start_bbs_listener_func = AsyncMock()
    router.node_auth.node_has_password_cache = AsyncMock(
        side_effect=RuntimeError("stop"))
    await router._process_mc_message_safe(dm("status"))
    session_mgr.create_session.assert_called_once()
