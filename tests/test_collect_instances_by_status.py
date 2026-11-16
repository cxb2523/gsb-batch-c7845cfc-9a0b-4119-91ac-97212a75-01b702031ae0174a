"""Batch instance collection: per-status settlement, scope, and identity."""

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp.exceptions import ToolError
from openstack import exceptions as sdk_exceptions

import mcp_openstack_ops.mcp_main  # noqa: F401  Register tools before importing them.
from mcp_openstack_ops import connection, user_connection
from mcp_openstack_ops.services import compute
from mcp_openstack_ops.tools import collect_instances_by_status as tool_module

CURRENT = 'project-a'
OTHER = 'project-b'


def _tool():
    tool = tool_module.collect_instances_by_status
    return getattr(tool, 'fn', tool)


def _server(server_id, project_id, status='ACTIVE'):
    return SimpleNamespace(
        id=server_id, name=server_id, status=status, project_id=project_id,
        flavor={}, image={'id': 'image-1'}, addresses={}, security_groups=[],
    )


def _stub_conn():
    conn = SimpleNamespace(
        current_project_id=CURRENT, compute=Mock(), identity=Mock(),
        image=Mock(), block_storage=Mock(),
    )
    conn.image.get_image.return_value = SimpleNamespace(name='cirros')
    return conn


@pytest.fixture
def request_identity(monkeypatch):
    """Multi-user mode with a stub connection for the request identity."""
    monkeypatch.setattr(user_connection, 'enabled', True)
    return _stub_conn()


@contextlib.contextmanager
def as_request_identity(conn):
    """Run the block inside a fake authenticated request identity."""
    identity = user_connection.RequestIdentity(
        conn, 'user-1', CURRENT, ('credential-id', 'secret'))
    token = user_connection._identity.set(identity)
    try:
        yield identity
    finally:
        user_connection._identity.reset(token)


def _servers_by_status(servers, failing=()):
    """Stub conn.compute.servers: per-status results, failures by status."""
    def servers_call(**kwargs):
        status = kwargs.get('status')
        if status in failing:
            raise RuntimeError('response body with credential-id secret')
        return [_server(sid, pid, status) for sid, pid in servers.get(status, ())]
    return servers_call


# --- normal collection --------------------------------------------------------

@pytest.mark.asyncio
async def test_collects_each_status_independently(request_identity):
    conn = request_identity
    conn.compute.servers.side_effect = _servers_by_status({
        'ACTIVE': [('a1', CURRENT)], 'ERROR': [('e1', CURRENT)]})

    with as_request_identity(conn):
        reply = json.loads(await _tool()(statuses=['active', 'ERROR']))

    assert reply['statuses_requested'] == ['ACTIVE', 'ERROR']
    assert reply['queries'] == [
        {'status': 'ACTIVE', 'success': True, 'instances_found': 1},
        {'status': 'ERROR', 'success': True, 'instances_found': 1},
    ]
    assert reply['succeeded_queries'] == 2
    assert reply['failed_queries'] == 0
    assert reply['instances_found'] == 2
    assert {row['id'] for row in reply['instances']} == {'a1', 'e1'}
    assert 'scope' not in reply
    for call in conn.compute.servers.call_args_list:
        assert call.kwargs['all_projects'] is False


@pytest.mark.asyncio
async def test_collection_uses_request_identity_not_env_or_cache(
    request_identity, monkeypatch,
):
    conn = request_identity
    monkeypatch.setattr(connection, '_connection_cache', Mock())
    monkeypatch.delenv('OS_AUTH_HOST', raising=False)
    conn.compute.servers.side_effect = _servers_by_status(
        {'ACTIVE': [('a1', CURRENT)]})

    with as_request_identity(conn):
        reply = json.loads(await _tool()(statuses=['ACTIVE']))

    assert reply['instances_found'] == 1
    connection._connection_cache.identity.get_token.assert_not_called()


# --- one failed status keeps the successful ones ------------------------------

@pytest.mark.asyncio
async def test_failed_status_does_not_discard_successes(request_identity):
    conn = request_identity
    conn.compute.servers.side_effect = _servers_by_status(
        {'ACTIVE': [('a1', CURRENT)], 'SHUTOFF': [('s1', CURRENT)]},
        failing={'ERROR'})

    with as_request_identity(conn):
        reply = json.loads(await _tool()(statuses=['ACTIVE', 'ERROR', 'SHUTOFF']))

    assert reply['succeeded_queries'] == 2
    assert reply['failed_queries'] == 1
    assert {row['id'] for row in reply['instances']} == {'a1', 's1'}
    failed = [query for query in reply['queries'] if not query['success']]
    assert failed == [
        {'status': 'ERROR', 'success': False, 'error_type': 'query_failed'}]
    # The failure is a safe type only: no exception text or response body.
    assert 'secret' not in json.dumps(reply)
    assert 'response body' not in json.dumps(reply)


# --- all_projects scope -------------------------------------------------------

@pytest.mark.asyncio
async def test_all_projects_reports_scope(request_identity, monkeypatch):
    monkeypatch.setenv('ALLOW_CROSS_PROJECT_READ', 'true')
    conn = request_identity
    conn.compute.servers.side_effect = _servers_by_status(
        {'ACTIVE': [('a', CURRENT), ('b', OTHER)]})

    with as_request_identity(conn):
        reply = json.loads(await _tool()(statuses=['ACTIVE'], all_projects=True))

    assert reply['all_projects'] is True
    assert reply['scope'] == {'all_projects': True, 'projects_in_result': 2}
    assert {row['id'] for row in reply['instances']} == {'a', 'b'}
    conn.compute.servers.assert_called_once_with(
        details=True, all_projects=True, status='ACTIVE')


@pytest.mark.asyncio
async def test_all_projects_narrowed_scope_is_flagged(request_identity, monkeypatch):
    monkeypatch.setenv('ALLOW_CROSS_PROJECT_READ', 'true')
    conn = request_identity
    conn.compute.servers.side_effect = _servers_by_status(
        {'ACTIVE': [('a', CURRENT)]})

    with as_request_identity(conn):
        reply = json.loads(await _tool()(statuses=['ACTIVE'], all_projects=True))

    assert reply['scope']['warning'] == connection.CROSS_PROJECT_NARROWED_WARNING


@pytest.mark.asyncio
async def test_all_projects_disabled_is_an_error_before_any_query(
    request_identity, monkeypatch,
):
    monkeypatch.delenv('ALLOW_CROSS_PROJECT_READ', raising=False)
    conn = request_identity

    with as_request_identity(conn):
        with pytest.raises(ToolError, match='ALLOW_CROSS_PROJECT_READ=true'):
            await _tool()(statuses=['ACTIVE'], all_projects=True)
    conn.compute.servers.assert_not_called()


@pytest.mark.asyncio
async def test_cross_project_policy_denial_fails_the_whole_call(
    request_identity, monkeypatch,
):
    monkeypatch.setenv('ALLOW_CROSS_PROJECT_READ', 'true')
    conn = request_identity
    conn.compute.servers.side_effect = sdk_exceptions.ForbiddenException(
        'Policy does not allow this')

    with as_request_identity(conn):
        with pytest.raises(ToolError, match='policy denied'):
            await _tool()(statuses=['ACTIVE', 'ERROR'], all_projects=True)


# --- identity and validation guards -------------------------------------------

@pytest.mark.asyncio
async def test_multi_user_without_request_identity_is_rejected(monkeypatch):
    monkeypatch.setattr(user_connection, 'enabled', True)
    token = user_connection._identity.set(None)
    try:
        with pytest.raises(ToolError, match='authenticated HTTP request'):
            await _tool()(statuses=['ACTIVE'])
    finally:
        user_connection._identity.reset(token)


@pytest.mark.asyncio
async def test_invalid_status_rejected_before_any_query(request_identity):
    conn = request_identity
    with as_request_identity(conn):
        with pytest.raises(ToolError, match='Invalid instance status'):
            await _tool()(statuses=['ACTIVE', 'NOT_A_STATUS'])
    conn.compute.servers.assert_not_called()


@pytest.mark.asyncio
async def test_empty_status_list_rejected_before_any_query(request_identity):
    conn = request_identity
    with as_request_identity(conn):
        with pytest.raises(ToolError, match='At least one'):
            await _tool()(statuses=[])
    conn.compute.servers.assert_not_called()


# --- service-level settlement -------------------------------------------------

def test_service_settles_each_status_and_counts_failures(request_identity):
    conn = request_identity
    conn.compute.servers.side_effect = _servers_by_status(
        {'ACTIVE': [('a1', CURRENT)]}, failing={'ERROR'})

    with as_request_identity(conn):
        result = compute.collect_instances_by_status(['ACTIVE', 'ERROR'])

    assert result['succeeded_queries'] == 1
    assert result['failed_queries'] == 1
    assert [row['id'] for row in result['instances']] == ['a1']
    assert result['queries'][1]['error_type'] == 'query_failed'


def test_service_deduplicates_and_validates_statuses():
    assert compute.normalize_instance_statuses(
        ['active', 'ACTIVE', ' error ']) == ['ACTIVE', 'ERROR']
    with pytest.raises(ValueError, match='Invalid instance status'):
        compute.normalize_instance_statuses(['BOGUS'])
    with pytest.raises(ValueError, match='At least one'):
        compute.normalize_instance_statuses([])
