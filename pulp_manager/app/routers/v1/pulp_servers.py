"""router for pulp_servers route
"""

# pylint: disable=too-many-arguments,unused-argument,redefined-builtin
from datetime import datetime
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException

from pulp_manager.app.auth import JWTBearer
from pulp_manager.app.config import CONFIG
from pulp_manager.app.database import get_session
from pulp_manager.app.models import RepoHealthStatus
from pulp_manager.app.repositories import (
    PulpServerRepository,
    PulpServerRepoRepository,
    PulpServerRepoTaskRepository,
    PulpServerRepoGroupRepository,
)
from pulp_manager.app.route import LoggingRoute, parse_route_args
from pulp_manager.app.schemas import (
    Page,
    PulpServer,
    PulpServerRepo,
    PulpServerSnapshotConfig,
    PulpServerSyncConfig,
    PulpServerRepoGroup,
    PulpServerRepoRemovalConfig,
    Task,
    PulpServerFindRepoPackageContent,
    PulpServerRemoveRepoContent,
)
from pulp_manager.app.job_manager import JobManager
from pulp_manager.app.services import PulpManager


pulp_server_v1_router = APIRouter(
    prefix="/v1/pulp_servers",
    tags=["pulp_servers"],
    responses={404: {"description": "Not Found"}},
    route_class=LoggingRoute,
)


@pulp_server_v1_router.get(
    "/", name="pulp_servers_v1:all", response_model=Page[PulpServer]
)
def get_all(
    name: Optional[str] = None,
    name__match: Optional[str] = None,
    repo_sync_health_rollup: Optional[str] = None,
    repo_sync_health_rollup_date__le: Optional[datetime] = None,
    repo_sync_health_rollup_date__ge: Optional[datetime] = None,
    snapshot_supported: Optional[bool] = None,
    sort_by: Optional[str] = None,
    order_by: Optional[str] = None,
    page: int = 1,
    page_size: int = CONFIG["paging"]["default_page_size"],
    db: get_session = Depends(),
):
    """Returns all pulp servers"""

    query_params = parse_route_args(**locals())
    return PulpServerRepository(db).filter_paged_result(**query_params)


@pulp_server_v1_router.get(
    "/repo_health_statuses",
    name="pulp_servers_v1:repo_health_statuses",
    response_model=List[str],
)
def get_repo_health_statuses():
    """Returns a list of repo health statuses"""

    return [repo_health_status.name for repo_health_status in RepoHealthStatus]


@pulp_server_v1_router.get(
    "/{id}", name="pulp_servers_v1:get_by_id", response_model=PulpServer
)
def get_server_by_id(id: int, db: get_session = Depends()):
    """Retuns pulp server given an ID #
    Args:
        id (int): id of pulp server
    """
    pulp_server = PulpServerRepository(db).get_by_id(id)

    if not pulp_server:
        raise HTTPException(status_code=404, detail="ID not found")

    return pulp_server


@pulp_server_v1_router.get(
    "/{id}/repos",
    name="pulp_servers_v1:get_repos_by_server_id",
    response_model=Page[PulpServerRepo],
)
def get_repos_by_server_id(
    id: int,
    name: Optional[str] = None,
    name__match: Optional[str] = None,
    repo_type: Optional[str] = None,
    repo_sync_health: Optional[str] = None,
    repo_sync_health_date__le: Optional[datetime] = None,
    repo_sync_health_date__ge: Optional[datetime] = None,
    sort_by: Optional[str] = None,
    order_by: Optional[str] = None,
    page: int = 1,
    page_size: int = CONFIG["paging"]["default_page_size"],
    db: get_session = Depends(),
):
    """Returns repos associated with server

    Args:
        id (int): id of pulp server
    """

    query_params = parse_route_args(**locals())
    # remove id field that comes through for pulp server, otherwise it gets added
    # to the kwargs and the wrong result is returned
    query_params["pulp_server_id"] = query_params["id"]
    del query_params["id"]

    pulp_server_repos_crud = PulpServerRepoRepository(db)
    paged_repos = pulp_server_repos_crud.filter_join_paged_result(True, **query_params)

    return paged_repos


@pulp_server_v1_router.get(
    "/{id}/repos/{repo_id}",
    name="pulp_servers_v1:get_repo_by_repo_id",
    response_model=PulpServerRepo,
)
def get_repo_by_repo_id(id: int, repo_id: int, db: get_session = Depends()):
    """Returns specific repo associated with server based on repo's id

    Args:
        id (int): id of pulp server
        pulp_server_repo_id (int): id of repo
    """
    pulp_server_repos_crud = PulpServerRepoRepository(db)
    repo = pulp_server_repos_crud.first(
        eager=["repo"], **{"pulp_server_id": id, "repo_id": repo_id}
    )

    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")

    return repo


@pulp_server_v1_router.post(
    "/{id}/repos/{repo_id}/find_package_content",
    name="pulp_servers_v1:find_package_content",
    response_model=List[Dict],
    status_code=201,
)
def find_package_content(
    id: int,
    repo_id: int,
    search: PulpServerFindRepoPackageContent,
    db: get_session = Depends(),
):
    """Searches the repository for for package content for the given search criteria. Searches
    the latest repo version that is held on Pulp. This call is made designed to make it easier to
    find duplicate or mismatched packages when an error similar to "A file located at the url
    https://pulp3.example.com/pulp/content/centos7-x86_64/snap-2023-06-r1-ext-centos7-microsoft/Packages/m/msopenjdk-17-17.0.3.x86_64.rpm
    failed validation due to checksum.
    Expected '5277f8cced86357449d9d446ca08f5d1487e084ba298fdee651f2d95eb9fefec',
    Actual '141ec88cf6a48b3c82efe1fb591c029d375f0c383abeeba745c17ccdb478db57'". Pulp repo versions
    can have tens of thousands of packages which is why at least one of name, version of sha256
    must be providied, to not have to deal with pagination options. For more detailed searching
    of a repo version the Pulp API should be used directly
    """

    pulp_server_repos_crud = PulpServerRepoRepository(db)
    repo = pulp_server_repos_crud.first(
        eager=["pulp_server"], **{"pulp_server_id": id, "repo_id": repo_id}
    )

    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")

    return PulpManager(db, repo.pulp_server.name).find_repo_package_content(
        repo.repo_href, **search.dict()
    )


@pulp_server_v1_router.post(
    "/{id}/repos/{repo_id}/remove_repo_content",
    name="pulp_servers_v1:remove_package_content",
    response_model=Task,
    status_code=201,
)
def remove_repo_content(
    id: int,
    repo_id: int,
    remove_schema: PulpServerRemoveRepoContent,
    db: get_session = Depends(),
):
    """Removes the specified content unit from the requested pulp srver repo"""

    pulp_server_repos_crud = PulpServerRepoRepository(db)
    repo = pulp_server_repos_crud.first(
        eager=["pulp_server", "repo"], **{"pulp_server_id": id, "repo_id": repo_id}
    )

    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")

    job_manager = JobManager(db)
    return job_manager.queue_remove_content_task(
        repo.pulp_server.name, repo.repo.name, **remove_schema.dict()
    )


# pylint: disable=too-many-locals
@pulp_server_v1_router.get(
    "/{id}/repos/{repo_id}/tasks",
    name="pulp_servers_v1:get_tasks_for_repo",
    response_model=Page[Task],
)
def get_tasks_for_repo(
    id: int,
    repo_id: int,
    task_type: Optional[str] = None,
    state: Optional[str] = None,
    worker_name: Optional[str] = None,
    date_queued__le: Optional[datetime] = None,
    date_queued__ge: Optional[datetime] = None,
    date_started__le: Optional[datetime] = None,
    date_started__ge: Optional[datetime] = None,
    date_finished__le: Optional[datetime] = None,
    date_finished__ge: Optional[datetime] = None,
    sort_by: Optional[str] = None,
    order_by: Optional[str] = None,
    page: int = 1,
    page_size: int = CONFIG["paging"]["default_page_size"],
    db: get_session = Depends(),
):
    """Returns repos associated with server

    Args:
        id (int): id of pulp server
    """

    query_params = parse_route_args(**locals())
    # remove the pulp server id otherwise will break the repo tak query
    query_params["pulp_server_id"] = query_params["id"]
    del query_params["id"]

    pulp_server_repos_crud = PulpServerRepoRepository(db)
    repo = pulp_server_repos_crud.first(
        eager=["repo"], **{"pulp_server_id": id, "repo_id": repo_id}
    )

    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")

    pulp_server_repo_task_crud = PulpServerRepoTaskRepository(db)
    # Pull back all the task objectrs and then build result object
    result = pulp_server_repo_task_crud.filter_join_paged_result(True, **query_params)

    tasks = [task.task for task in result["items"]]

    return {
        "items": tasks,
        "page": page,
        "page_size": page_size,
        "total": result["total"],
    }


@pulp_server_v1_router.get(
    "/{id}/repo_groups",
    name="pulp_servers_v1:get_repo_groups_for_server",
    response_model=Page[PulpServerRepoGroup],
)
def get_repo_groups_by_server_id(
    id: int,
    page: int = 1,
    page_size: int = CONFIG["paging"]["default_page_size"],
    db: get_session = Depends(),
):
    """Returns repos associated with server

    Args:
        id (int): id of pulp server
    """

    query_params = parse_route_args(**locals())
    # remove the pulp server id otherwise will break the repo tak query
    query_params["pulp_server_id"] = query_params["id"]
    del query_params["id"]

    repo_groups_crud = PulpServerRepoGroupRepository(db)
    paged_repo_groups = repo_groups_crud.filter_join_paged_result(True, **query_params)

    return paged_repo_groups


@pulp_server_v1_router.get(
    "/{id}/repo_groups/{repo_group_id}",
    name="pulp_servers_v1:get_repo_groups_for_server_by_group_id",
    response_model=PulpServerRepoGroup,
)
def get_repo_groups_by_group_id(
    id: int, repo_group_id: int, db: get_session = Depends()
):
    """Returns repos associated with server

    Args:
        id (int): id of pulp server
    """
    repo_groups_crud = PulpServerRepoGroupRepository(db)
    repo_group = repo_groups_crud.first(
        eager=["repo_group"], **{"pulp_server_id": id, "repo_group_id": repo_group_id}
    )

    if repo_group is None:
        raise HTTPException(status_code=404, detail="Repo group not found invalid")

    return repo_group


@pulp_server_v1_router.post(
    "/{id}/snapshot_repos",
    name="pulp_servers_v1:snapshot_repos",
    response_model=Task,
    status_code=201,
    dependencies=[
        Depends(JWTBearer(allowed_groups=CONFIG["auth"]["admin_group"].split(",")))
    ],
)
def snapshot_repos(
    id: int, snapshot_config: PulpServerSnapshotConfig, db: get_session = Depends()
):
    """Queues a job to snapshot the specified pulp server repos pulp server repos.
    If the name of the snapshot does not being with snap- it is added to the start
    of the snapshot name"""

    pulp_server = PulpServerRepository(db).get_by_id(id)

    if pulp_server is None:
        raise HTTPException(status_code=404, detail="Pulp server not found")
    job_manager = JobManager(db)

    if not snapshot_config.snapshot_prefix.startswith("snap-"):
        snapshot_config.snapshot_prefix = f"snap-{snapshot_config.snapshot_prefix}"

    return job_manager.queue_snapshot_task(pulp_server.name, **snapshot_config.dict())


@pulp_server_v1_router.post(
    "/{id}/sync_repos",
    name="pulp_servers_v1:sync_repos",
    response_model=Task,
    status_code=201,
)
def sync_repos(id: int, sync_config: PulpServerSyncConfig, db: get_session = Depends()):
    """Queues a repo sync job against the specified pulp server"""

    pulp_server = PulpServerRepository(db).get_by_id(id)

    if pulp_server is None:
        raise HTTPException(status_code=404, detail="Pulp server not found")

    if sync_config.max_concurrent_syncs <= 0:
        raise HTTPException(
            status_code=400,
            detail="max_concurrent_syncs cannot less than or equal to 0",
        )

    job_manager = JobManager(db)

    return job_manager.queue_sync_repo_task(pulp_server.name, **sync_config.dict())


@pulp_server_v1_router.post(
    "/{id}/remove_repos",
    name="pulp_servers_v1:remove_repos",
    response_model=Task,
    status_code=201,
    dependencies=[
        Depends(JWTBearer(allowed_groups=CONFIG["auth"]["admin_group"].split(",")))
    ],
)
def remove_repos(
    id: int, removal_config: PulpServerRepoRemovalConfig, db: get_session = Depends()
):
    """Queues a job to remove repositories from the specified
    Pulp server based on the provided configuration."""

    pulp_server = PulpServerRepository(db).get_by_id(id)

    if pulp_server is None:
        raise HTTPException(status_code=404, detail="Pulp server not found")

    job_manager = JobManager(db)

    return job_manager.queue_removal_task(pulp_server.name, **removal_config.dict())
