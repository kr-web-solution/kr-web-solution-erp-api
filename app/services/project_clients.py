"""Backwards-compatible re-exports for project ↔ client membership."""

from .client_project import (  # noqa: F401
    add_client_to_project,
    assert_client_on_project,
    assert_clients_exist,
    create_project,
    delete_client,
    delete_project,
    normalize_client_ids,
    project_has_client,
    remove_client_from_project,
    require_project_for_billing,
    set_project_clients,
    sole_client_projects,
)
