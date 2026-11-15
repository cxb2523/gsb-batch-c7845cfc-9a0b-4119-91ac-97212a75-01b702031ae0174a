"""Tests for automatic connector registration during package export.

Covers the scenario where a Flow contains OpenApiConnection actions
(e.g. SharePoint Online / Dropbox) but the user forgot to call the
corresponding set_*_connector() method: exporting the zip must still
register the connector so the imported package has no missing connections.
"""
import json
import zipfile

import pytest

from pypowerautomate.flow import Flow
from pypowerautomate.package import Package
from pypowerautomate.actions.sharepoint import (
    SharepointCreateFileAction,
    SharepointGetFileContentAction,
)
from pypowerautomate.actions.dropbox import DropboxCreateFileAction
from pypowerautomate.actions.variable import InitVariableAction, VariableTypes

SHAREPOINT_API = "shared_sharepointonline"
DROPBOX_API = "shared_dropbox"


def read_package(zip_path):
    """Reads the exported package zip and returns its key files."""
    with zipfile.ZipFile(zip_path) as z:
        manifest = json.loads(z.read("manifest.json"))
        definition_entry = next(
            n for n in z.namelist()
            if n.startswith("Microsoft.Flow/flows/") and n.endswith("/definition.json")
        )
        flow_dir = definition_entry.rsplit("/", 1)[0]
        apis_map = json.loads(z.read(f"{flow_dir}/apisMap.json"))
        connections_map = json.loads(z.read(f"{flow_dir}/connectionsMap.json"))
        definition = json.loads(z.read(definition_entry))
    return manifest, apis_map, connections_map, definition


def export(flow, tmp_path, name="test_package", configure=None):
    """Builds a Package, applies optional user configuration, exports the zip."""
    package = Package(name, flow)
    if configure:
        configure(package)
    zip_path = package.export_zipfile(str(tmp_path))
    return read_package(zip_path)


def api_resources(manifest, api_name):
    """Returns the API resources in the manifest with the given API name."""
    return [
        r for r in manifest["resources"].values()
        if r["type"] == "Microsoft.PowerApps/apis" and r.get("name") == api_name
    ]


def connection_resources(manifest):
    """Returns all connection resources in the manifest."""
    return [
        r for r in manifest["resources"].values()
        if r["type"] == "Microsoft.PowerApps/apis/connections"
    ]


def make_sharepoint_flow():
    flow = Flow()
    flow.append_action(SharepointCreateFileAction(
        "Create_file", "dataset", "/folder", "a.txt", "body"))
    flow.append_action(SharepointGetFileContentAction(
        "Get_content", "dataset", "file-id", True))
    return flow


def test_sharepoint_actions_auto_register_connector(tmp_path):
    """SharePoint actions without set_sharepoint_connector() still produce a
    complete, consistent package."""
    manifest, apis_map, connections_map, definition = export(
        make_sharepoint_flow(), tmp_path)

    # Exactly one SharePoint API resource and one connection resource,
    # even though the flow contains two SharePoint actions.
    apis = api_resources(manifest, SHAREPOINT_API)
    assert len(apis) == 1
    connections = connection_resources(manifest)
    assert len(connections) == 1
    assert connections[0]["dependsOn"] == list(apis_map.values())

    # apisMap / connectionsMap point at the registered resources.
    assert set(apis_map) == {SHAREPOINT_API}
    assert set(connections_map) == {SHAREPOINT_API}
    assert manifest["resources"][apis_map[SHAREPOINT_API]]["name"] == SHAREPOINT_API
    assert (manifest["resources"][connections_map[SHAREPOINT_API]]["type"]
            == "Microsoft.PowerApps/apis/connections")

    # connectionReferences is consistent with connectionsMap
    # (empty here because no existing connection name was given).
    connection_refs = definition["properties"]["connectionReferences"]
    assert set(connection_refs) <= set(connections_map)

    # Dropbox was never used and must not be registered.
    assert DROPBOX_API not in apis_map
    assert DROPBOX_API not in connections_map
    assert api_resources(manifest, DROPBOX_API) == []


def test_explicit_connection_name_is_preserved(tmp_path):
    """A connection name set via set_sharepoint_connector() must survive
    auto-registration and must not create duplicate resources."""
    manifest, apis_map, connections_map, definition = export(
        make_sharepoint_flow(), tmp_path,
        configure=lambda p: p.set_sharepoint_connector("my_existing_connection"))

    # No duplicate resources from auto-registration.
    assert len(api_resources(manifest, SHAREPOINT_API)) == 1
    assert len(connection_resources(manifest)) == 1

    # The user's connection name is used in connectionReferences.
    connection_refs = definition["properties"]["connectionReferences"]
    assert set(connection_refs) == {SHAREPOINT_API}
    ref = connection_refs[SHAREPOINT_API]
    assert ref["connectionName"] == "my_existing_connection"
    assert ref["id"] == "/providers/Microsoft.PowerApps/apis/shared_sharepointonline"

    # connectionReferences and connectionsMap cover the same connectors.
    assert set(connection_refs) == set(connections_map)


def test_dropbox_only_flow_registers_no_sharepoint(tmp_path):
    """A flow with only Dropbox actions registers Dropbox and nothing else."""
    flow = Flow()
    flow.append_action(DropboxCreateFileAction(
        "Create_dropbox_file", "/folder", "a.txt", "body"))

    manifest, apis_map, connections_map, definition = export(flow, tmp_path)

    assert set(apis_map) == {DROPBOX_API}
    assert set(connections_map) == {DROPBOX_API}
    assert len(api_resources(manifest, DROPBOX_API)) == 1
    assert len(connection_resources(manifest)) == 1

    # No SharePoint artifacts anywhere.
    assert SHAREPOINT_API not in apis_map
    assert SHAREPOINT_API not in connections_map
    assert api_resources(manifest, SHAREPOINT_API) == []
    assert SHAREPOINT_API not in definition["properties"]["connectionReferences"]


def test_mixed_flow_registers_each_connector_once(tmp_path):
    """SharePoint + Dropbox actions register both connectors, each exactly once."""
    flow = Flow()
    flow.append_action(SharepointCreateFileAction(
        "Create_file", "dataset", "/folder", "a.txt", "body"))
    flow.append_action(DropboxCreateFileAction(
        "Create_dropbox_file", "/folder", "a.txt", "body"))
    flow.append_action(SharepointGetFileContentAction(
        "Get_content", "dataset", "file-id", True))

    manifest, apis_map, connections_map, definition = export(flow, tmp_path)

    assert set(apis_map) == {SHAREPOINT_API, DROPBOX_API}
    assert set(connections_map) == {SHAREPOINT_API, DROPBOX_API}
    assert len(api_resources(manifest, SHAREPOINT_API)) == 1
    assert len(api_resources(manifest, DROPBOX_API)) == 1
    assert len(connection_resources(manifest)) == 2


def test_flow_without_openapi_actions_registers_nothing(tmp_path):
    """A flow with no OpenApiConnection actions gets no connector resources."""
    flow = Flow()
    flow.append_action(InitVariableAction(
        "Init_var", "myVar", VariableTypes.string, "hello"))

    manifest, apis_map, connections_map, definition = export(flow, tmp_path)

    assert apis_map == {}
    assert connections_map == {}
    assert connection_resources(manifest) == []
    assert definition["properties"]["connectionReferences"] == {}
    # Only the flow resource itself remains in the manifest.
    assert all(
        r["type"] == "Microsoft.Flow/flows"
        for r in manifest["resources"].values()
    )
