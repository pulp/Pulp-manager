"""Contains service that reconciles repos that are on the pulp server
against what is held in the database
"""
import re
from collections import namedtuple
from sqlalchemy.orm import Session

from pulp3_bindings.pulp3.repositories import get_all_repos
from pulp3_bindings.pulp3.remotes import get_all_remotes
from pulp3_bindings.pulp3.distributions import get_all_distributions

from pulp_manager.app.exceptions import PulpManagerEntityNotFoundError
from pulp_manager.app.repositories import (
    PulpServerRepository, RepoRepository, PulpServerRepoRepository
)
from pulp_manager.app.services.base import PulpServerService
from pulp_manager.app.utils import log
from .pulp_helpers import new_pulp_client


# Create an PulpRepoInstance namedtypled to hold the linking of a repo, remote
# and a distribution
PulpRepoInstance = namedtuple(
    "PulpRepoInstance",
    "name repo_href remote_href remote_feed distribution_href"
)


class PulpReconciler(PulpServerService):
    """Reconciles the repos that exist on a pulp server with what is storred
    in the DB
    """

    def __init__(self, db: Session, name: str):
        """Constructor
        :param db: DB session to use
        :type db: Session
        :param name: name of the pulp server the reconciler will manage
        :type name: str
        """

        self._db = db
        self._pulp_server_crud = PulpServerRepository(db)
        self._repo_crud = RepoRepository(db)
        self._pulp_server_repo_crud = PulpServerRepoRepository(db)

        pulp_server_result = self._pulp_server_crud.get_pulp_server_with_repos(**{"name": name})
        if len(pulp_server_result) == 0:
            raise PulpManagerEntityNotFoundError(f"pulp server {name} not found")

        self._pulp_server = pulp_server_result[0]

    def _get_pulp_server_repo_instances(self):
        """Returns a dict of PulpRepoInstances which map to the repos held on the pulp server.
        Along with any remote and distributions that have been configured. Key is the name of the
        repo and the value is the PulpRepoInstance
        :return: dict
        """

        client = new_pulp_client(self._pulp_server)

        repos = get_all_repos(client)
        remotes = get_all_remotes(client)
        distributions = get_all_distributions(client)

        repo_dict = {}
        remote_dict_by_href = {}
        remote_dict_by_name = {}
        distribution_dict = {}

        for repo in repos:
            repo_dict[repo.name] = repo

        for remote in remotes:
            remote_dict_by_name[remote.name] = remote
            remote_dict_by_href[remote.pulp_href] = remote

        for distribution in distributions:
            distribution_dict[distribution.name] = distribution

        repo_instances = {}
        for name, repo in repo_dict.items():
            # Get remote info from repo.remote if available, otherwise fall back to name matching
            remote_href = None
            remote_feed = None
            if repo.remote and repo.remote in remote_dict_by_href:
                remote_href = remote_dict_by_href[repo.remote].pulp_href
                remote_feed = remote_dict_by_href[repo.remote].url
            elif name in remote_dict_by_name:
                remote_href = remote_dict_by_name[name].pulp_href
                remote_feed = remote_dict_by_name[name].url

            pulp_repo_instance = PulpRepoInstance(
                name,
                repo.pulp_href,
                remote_href,
                remote_feed,
                distribution_dict[name].pulp_href if name in distribution_dict else None
            )
            repo_instances[name] = pulp_repo_instance
        return repo_instances

    def _add_missing_repos(self, pulp_repos: dict):
        """Given a dict of repos that exist on a pulp server, adds any names
        to the database that are missing. Returns a dict of all repos held in
        pulp_manager. Key is the name of the repo and the value Repo entity

        :param pulp_repos: dict of repos that exist in the pulp server. Key is the name
                           of the repo and value is the PulpRepoInstance
        :type pulp_repos: dict
        :return: dict
        """

        log.info("Starting repo name reconciliation")
        repo_names = pulp_repos.keys()
        known_repos = self._repo_crud.filter()
        known_repo_names = [repo.name for repo in known_repos]
        missing_repo_names = list(set(repo_names) - set(known_repo_names))

        if len(missing_repo_names) > 0:
            bulk_add_repo_config = []
            log.info(f"There are {missing_repo_names} repo names to add")
            #pylint: disable=unused-variable
            for repo_name, repo in pulp_repos.items():
                if repo.name in missing_repo_names:
                    # Pulp href will be in the following format /pulp/api/v3/remotes/deb/apt/...
                    # from this able to extract the repo type
                    repo_match = re.match('/pulp/api/v3/repositories/([a-z]+)/', repo.repo_href)
                    bulk_add_repo_config.append({
                        "name": repo.name, "repo_type": repo_match.groups()[0]
                    })

            try:
                self._repo_crud.bulk_add(bulk_add_repo_config)
                self._db.commit()
            except Exception:
                log.exception("failed to add repos to db")
                self._db.rollback()

            known_repos = self._repo_crud.filter()

        log.info("Repo name reconciliation complete")
        repos = {}
        for repo in known_repos:
            repos[repo.name] = repo
        return repos

    def _calculate_repos_to_add(self, repos: dict, pulp_repo_instances: dict):
        """Returns a list of dicts where each dict represents a PulpServerRepoEntity
        that needs to be added to the database

        :param repos: dict of repos that exist in pulp_manager. Key is the name of the
                      repo and the value is the Repo entity
        :type repos: dict
        :param pulp_repo_instances: Dict of repos instances which contains, repository,
                                    remote and distribution information
        :type pulp_server_instance_repos: dict
        :return: list
        """

        existing_pulp_server_repos_ids = [
            pulp_server_repo.repo_id for pulp_server_repo in self._pulp_server.repos
        ]
        pulp_server_repos_to_add = []

        for repo_name, repo in pulp_repo_instances.items():
            if repos[repo_name].id in existing_pulp_server_repos_ids:
                continue

            pulp_server_repos_to_add.append({
                "pulp_server_id": self._pulp_server.id,
                "repo_id": repos[repo_name].id,
                "repo_href": repo.repo_href,
                "remote_href": repo.remote_href,
                "remote_feed": repo.remote_feed,
                "distribution_href": repo.distribution_href
            })

        return pulp_server_repos_to_add

    def _calculate_repos_to_update(self, pulp_repo_instances: dict):
        """Returns a list of dicts where each dict represents the fields to update a
        PulpServerRepo entity.

        :param pulp_repo_instances: Dict where the key is the name of the
                                    repo and the value is the PulpRepoInstance
        :type pulp_repo_instances: dict
        :return: list
        """

        repos_to_update = []

        for repo in self._pulp_server.repos:
            repo_updates = {}
            repo_name = repo.repo.name
            if repo_name not in pulp_repo_instances:
                continue

            repo_config = pulp_repo_instances[repo_name]._asdict()
            for key, value in repo_config.items():
                try:
                    if value != getattr(repo, key):
                        repo_updates[key] = value
                except AttributeError:
                    # Don't care about attribute errors as it will be a property
                    # on the PulpRepoInstance, which isn't stored on the PulpServerRepo
                    pass

            if len(repo_updates) > 0:
                repo_updates["id"] = repo.id
                repos_to_update.append(repo_updates)

        return repos_to_update

    def _calculate_repos_to_delete(self, pulp_repo_instances: dict):
        """Return a list of PulpServerRepo entites that should be removed from the db

        :param pulp_repo_instances: Dict where the key is the name of the
                                    repo and the value is the PulpRepoInstance
        :type pulp_repo_instances: dict
        :return: list
        """

        repos_to_delete = []

        for repo in self._pulp_server.repos:
            repo_name = repo.repo.name
            if repo_name not in pulp_repo_instances:
                repos_to_delete.append(repo)

        return repos_to_delete

    def reconcile(self):
        """Retrieves the repos that exist on the pulp server itself from the PulpServer entity
        and then updates the pulp_manager DB with the repos that exist on the pulp server
        :return: PulpServer
        """

        log.info(f"reconciling repos for {self._pulp_server.name}")
        pulp_repo_instances = self._get_pulp_server_repo_instances()
        pulp_manager_repos = self._add_missing_repos(pulp_repo_instances)

        pulp_server_repos_to_add = self._calculate_repos_to_add(
            pulp_manager_repos, pulp_repo_instances
        )
        pulp_server_repos_to_update = self._calculate_repos_to_update(pulp_repo_instances)
        pulp_server_repos_to_delete = self._calculate_repos_to_delete(pulp_repo_instances)

        log.debug(f"{self._pulp_server.name} {len(pulp_server_repos_to_add)} repos to add")
        log.debug(f"{self._pulp_server.name} {len(pulp_server_repos_to_update)} repos to update")
        log.debug(f"{self._pulp_server.name} {len(pulp_server_repos_to_delete)} repos to delete")

        try:
            if len(pulp_server_repos_to_add) > 0:
                self._pulp_server_repo_crud.bulk_add(pulp_server_repos_to_add)

            if len(pulp_server_repos_to_update) > 0:
                self._pulp_server_repo_crud.bulk_update(pulp_server_repos_to_update)

            if len(pulp_server_repos_to_delete) > 0:
                for pulp_server_repo in pulp_server_repos_to_delete:
                    self._pulp_server_repo_crud.delete(pulp_server_repo)

            self._db.commit()
        except Exception:
            log.exception("Error updating repos for {self._pulp_server.name}")
            self._db.rollback()
            raise

        self._pulp_server = self._pulp_server_crud.get_pulp_server_with_repos(
            **{"id": self._pulp_server.id}
        )[0]
        log.info(f"successfully reconciled repos for {self._pulp_server.name}")
        self._db.refresh(self._pulp_server)

        return self._pulp_server
