"""Pulp manager carries out creation of repos and their distributions
"""

# pylint: disable=too-many-lines
import os
import re
import traceback
from typing import List
import requests
from sqlalchemy import exc
from sqlalchemy.orm import Session

from pulp3_bindings.pulp3 import Pulp3Client
from pulp3_bindings.pulp3.resources import (
    FileRepository,
    DebRepository,
    Distribution,
    Remote,
    Repository,
)
from pulp3_bindings.pulp3.remotes import (
    get_all_remotes,
    new_remote,
    update_remote_monitor,
    get_remote_class,
)
from pulp3_bindings.pulp3.repositories import (
    get_repo_class,
    get_all_repos,
    new_repo,
    update_repo_monitor,
    delete_repo_monitor,
    get_repo,
)
from pulp3_bindings.pulp3.distributions import (
    get_all_distributions,
    get_distribution_class,
    new_distribution_monitor,
    update_distribution_monitor,
)
from pulp3_bindings.pulp3.publications import new_publication, get_publication_class
from pulp3_bindings.pulp3.signing_services import get_all_signing_services

from pulp_manager.app.config import CONFIG
from pulp_manager.app.exceptions import (
    PulpManagerEntityNotFoundError,
    PulpManagerError,
    PulpManagerValueError,
)
from pulp_manager.app.repositories import (
    PulpServerRepository,
    RepoRepository,
    PulpServerRepoRepository,
)
from pulp_manager.app.services.base import PulpServerService
from pulp_manager.app.utils import log
from .pulp_helpers import new_pulp_client, get_repo_type_from_href, delete_by_href


SUPPORTED_REPO_TYPES = ["rpm", "deb", "file", "python", "container"]


# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-locals
class PulpManager(PulpServerService):
    """Carries out creation and updates of repos and their associated distribtuions
    and publications
    """

    def __init__(self, db: Session, name: str):
        """Constructor
        :param db: DB session to use
        :type db: Session
        :param name: name of the pulp server to manage
        :type name: str
        """

        self._db = db
        self._pulp_server_crud = PulpServerRepository(db)
        self._repo_crud = RepoRepository(db)
        self._pulp_server_repo_crud = PulpServerRepoRepository(db)

        pulp_server_search = self._pulp_server_crud.get_pulp_server_with_repos(
            **{"name": name}
        )

        if len(pulp_server_search) == 0:
            raise PulpManagerEntityNotFoundError(
                f"pulp server {name} not found")

        self._pulp_server = pulp_server_search[0]
        self._pulp_client = new_pulp_client(self._pulp_server)
        self._deb_signing_service_href = self._get_deb_signing_service()

        # Settings for polling pulp updates, wait for up to 15 mins
        self._poll_interval_sec = 1
        self._max_wait_count = 900

        self._root_ca = None
        self._get_root_ca()

    def _get_deb_signing_service(self):
        """Returns the href to the deb signing service, which is set in config.ini. If not
        set None is returned

        :param client: authenticated pulp3 client to get the signing service from
        :type client: Pulp3Client
        :return: str
        """

        if "pulp" in CONFIG and "deb_signing_service" in CONFIG["pulp"]:
            signing_services = get_all_signing_services(
                self._pulp_client,
                params={"name": CONFIG["pulp"]["deb_signing_service"]},
            )

            if len(signing_services) == 0:
                raise PulpManagerError(
                    f"could not find signing service {CONFIG['pulp']['deb_signing_service']}"
                )

            return signing_services[0].pulp_href

        return None

    def _get_root_ca(self):
        """If environment variable PULP_MANAGER_CA_FILE is set or a ca file has been specified
        in config.ini then the file with the root CA cert in is read and set to _root_ca
        """

        # pylint: disable=unspecified-encoding
        if "PULP_MANAGER_CA_FILE" in os.environ:
            with open(os.getenv("PULP_MANAGER_CA_FILE"), "r") as root_ca_file:
                self._root_ca = root_ca_file.read()
        elif "ca" in CONFIG and "root_ca_file_path" in CONFIG["ca"]:
            with open(CONFIG["ca"]["root_ca_file_path"], "r") as root_ca_file:
                self._root_ca = root_ca_file.read()

    def _get_or_create_pm_repo(self, name: str, repo_type: str):
        """Gets of creates the repo in the pulp manager database if it doesn't exist
        and returns the entity

        :param name: name of the pulp repo
        :type name: str
        :param repo_type: repo type e.g. rpm
        :type repo_type: str
        :return: Repo
        """

        pulp_manager_repo_search = self._repo_crud.first(**{"name": name})
        if pulp_manager_repo_search is None:
            log.debug(f"adding repo {name} to database")
            pm_repo = self._repo_crud.add(
                **{"name": name, "repo_type": repo_type})
            self._db.commit()
            return pm_repo

        return pulp_manager_repo_search

    def _generate_base_path(self, name: str, base_url: str):
        """Generates the base path for a distribution

        :param name: name of the distribution the base url is being generated for
        :type name: str
        :param base_url: base url for the distribution to form the base path. e.g. el7-x86_64
        :type base_url: str
        :return: str
        """

        if not base_url.endswith("/"):
            base_url = f"{base_url}/"

        return self._process_package_name(name, base_url)

    def _process_package_name(self, name: str, base_url: str):
        # Extract the pattern and replacement rule from the configuration, or just keep the input same in output (default)
        pattern=CONFIG["pulp"]["package_name_replacement_pattern"]
        replacement=CONFIG["pulp"]["package_name_replacement_rule"]
        if not pattern:
            return f"{base_url}{name}"

        package_rename_config = {
            "pattern": re.compile(pattern),
            "replacement_rule": re.compile(replacement)
        }
        pattern = package_rename_config['pattern']
        replacement_rule = package_rename_config['replacement_rule']
        
        # Perform the regex match
        match = re.match(pattern, name)
        if match:
            # Extract named groups from the match
            match_dict = match.groupdict()

            # Format the updated name using the replacement rule
            updated_name = replacement_rule.format(**match_dict)
            return f"{base_url}{updated_name}"
        
        return f"{base_url}{name}"

    def create_publication_from_repo_version(
        self, repo_version_href: str, repo_type: str, is_deb_flat_repo: bool
    ):
        """Creates a publication for the given repo version href. Returns the
        pulp3.resources.Task object to track creation

        :param repo_version_href: version of the repo to publish
        :type repo_version_href: str
        :param repo_type: type of repo publication is being created for e..g rpm
        :type repo_type: str
        :param is_deb_flat_repo: Indicates that the deb repo is a flat repo (synced without dist).
                                 When this is set to treu, it changes the publication options for
                                 a deb, so that structured is set to False and simple is set to
                                 True
        :type is_deb_flat_repo: bool
        :return: pulp3.resources.Task
        """

        publication_config = {"repository_version": repo_version_href}

        if repo_type == "rpm":
            publication_config.update(
                {"metadata_checksum_type": "sha256",
                    "package_checksum_type": "sha256"}
            )
        elif repo_type == "deb" and not is_deb_flat_repo:
            publication_config["structured"] = True
        elif repo_type == "deb" and is_deb_flat_repo:
            publication_config.update({"structured": False, "simple": True})

        publication_class = get_publication_class(repo_type)
        publication = publication_class(**publication_config)

        task = new_publication(self._pulp_client, publication)
        return task

    def create_repo(
        self, name: str, description: str, repo_type: str, remote_href: str = None
    ):
        """Creates a new repo on the pulp server and also adds the repo entry to the database.
        If a deb signing service has been added to config.ini then the deb signing service
        url is added to the repo. Returns the pulp3 Repository object

        :param name: name of the repo to create on the pulp server
        :type name: str
        :param description: description of the repo, must contain base_url: followed by the path
                            to use for the base_url e.g. el7-x86_64
        :type description: str
        :param remote_href: href of the remote the repo is linked to
        :type remote_href: str
        :return: Repository
        """

        repo_class = get_repo_class(repo_type)
        repo_config = {"name": name,
                       "description": description, "remote": remote_href}
        if repo_type == "deb" and self._deb_signing_service_href:
            repo_config["signing_service"] = self._deb_signing_service_href

        log.debug(f"create repo on {self._pulp_server.name}, {repo_config}")
        pulp_repo = repo_class(**repo_config)
        new_repo(self._pulp_client, pulp_repo)
        return pulp_repo

    def delete_repository(self, repository: PulpServerRepoRepository):
        """
        Deletes the specified repository from the pulp server and monitors the deletion task.

        This method first attempts to delete the given repository
        and then monitors the deletion task
        until it is completed. It logs the attempt
        and either returns the Task object upon successful
        deletion or raises an error if the deletion fails.

        Parameters:
            repository (Repository): The repository to delete.

        Raises:
            Exception: General exception raised if the deletion task fails.
        """

        log.debug(
            f"Attempting to delete repository {repository.repo_href} on {self._pulp_server.name}"
        )

        try:
            delete_by_href(self._pulp_client, repository.pulp_href)
        except Exception as e:
            log.error(f"Error deleting repository: {e}")
            raise

        # Monitor the deletion task until completion
        try:
            delete_repo_monitor(self._pulp_client, repository)
            log.info(
                f"Successfully deleted and monitored repository {repository.pulp_href}"
            )
        except Exception as e:
            log.error(
                f"Deletion task monitoring failed for repository {repository.pulp_href}: {e}"
            )
            raise

    def update_repo(self, pulp_repo: Repository, description: str, remote_href: str):
        """Updates a repo on the pulp server

        :param pulp_repo: pulp repo to update
        :param pulp_repo: Repostiory
        :param description: description to attach to repo
        :type description: str
        :param remote_href: href of pulp remote to link the repo to
        :type remote_href: str
        """

        log.debug(
            f"attempting to update repo {pulp_repo.pulp_href} with name {pulp_repo.name} "
            f"for {self._pulp_server.name}"
        )

        updates_needed = False
        if pulp_repo.description != description:
            pulp_repo.description = description
            updates_needed = True

        if pulp_repo.remote != remote_href:
            pulp_repo.remote = remote_href
            updates_needed = True

        if (
            isinstance(pulp_repo, DebRepository)
            and self._deb_signing_service_href
            and self._deb_signing_service_href != pulp_repo.signing_service
        ):
            pulp_repo.signing_service = self._deb_signing_service_href
            updates_needed = True

        if updates_needed:
            log.debug(
                f"repo {pulp_repo.pulp_href} and name {pulp_repo.name} requires updates"
            )
            update_repo_monitor(
                self._pulp_client,
                pulp_repo,
                poll_interval_sec=self._poll_interval_sec,
                max_wait_count=self._max_wait_count,
            )
        else:
            log.debug(
                f"repo {pulp_repo.pulp_href} and name {pulp_repo.name} requires not updates"
            )

    def create_remote(
        self,
        name: str,
        url: str,
        remote_type: str,
        ca_cert: str = None,
        client_cert: str = None,
        client_key: str = None,
        username: str = None,
        password: str = None,
        proxy_url: str = None,
        tls_validation=False,
        distributions: str = None,
        components: str = None,
        architectures: str = None,
        ignore_missing_package_indices: bool = False,
    ):
        """Creates a new remote on the pulp server. TLS validation by default, but if the URL to sync
        from is not an internal URL, then TLS validation can be disabled via config option `remote_tls_validation`.

        :param name: name of the remote to create
        :type name: str
        :param url: url to download repo from
        :type url: str
        :param remote_type: type of remote to create, e.g. rpm
        :type remote_type: str
        :param ca_cert: A PEM encoded CA certificate used to validate the server certificate
                        presented by the remote server. This is needed for RHEL as there is no
                        SSL interception on the proxy for RedHat otherwise it breaks auth
        :type ca_cert: str
        :param client_cert: A PEM encoded client certificate used for authentication. This is
                            needed when synching repos from RedHat
        :type client_cert: str
        :param client_key: A PEM encoded private key used for authentication. This is needed
            when synching repos from RedHat
        :type client_key: str
                :param username: username to use for authentication when synching.
                :type username: str
                :param password: The password to be used for authentication when syncing.
        :param proxy_url: The proxy URL. Format: scheme://host:port
        :type proxy_url: str
        :param tls_validation: If True, TLS peer validation must be performed. Required for redhat
        :type tls_validation: bool
        :param distributions: Only valid for deb repositories, if provided for any other
                              repo type the option is ignored. Whitespace separated list of
                              distributions to sync. The distribution is the path from the
                              repository root to the "Release" file you want to access. This is
                              often, but not always, equal to either the codename or the suite of
                              the release you want to sync. If the repository you are trying to sync
                              uses "flat repository format", the distribution must end with a "/".
                              Based on "/etc/apt/sources.list" syntax.
        :type distributions: str
        :param components: Only valid for deb repositories, if provided for any other
                           repo type the option is ignored. Whitespace separatet list of components
                           to sync. If none are supplied, all that are available will be
                           synchronized. Leave blank for repositores using "flat repository
                           format".
        :type components: str
        :param architectures: Only valid for deb repositories, if provided for any other
                              repo type the option is ignored. Whitespace separated list of
                              architectures to sync If none are supplied, all that are available
                              will be synchronized. A list of valid architecture specification
                              strings can be found by running "dpkg-architecture -L". A sync will
                              download the intersection of the list of architectures provided via
                              this field and those provided by the relevant "Release" file.
                              Architecture="all" is always synchronized and does not need to be
                              provided here.
        :type architectures: str
        :param ignore_missing_package_indices: Only valid for deb repositories, By default, upstream
                                               repositories that declare architectures and
                                               corresponding package indices in their Release files
                                               without actually publishing them, will fail to
                                               synchronize. Set this flag to True to allow the
                                               synchronization of such "partial mirrors" instead.
                                               Alternatively, you could make your remote filter by
                                               architectures for which the upstream repository does
                                               have indices
        :type ignore_missing_package_indices: bool
        :return: pulp3.resources.Remote
        """

        log.debug(
            f"attempting to create remote {name} on {self._pulp_server.name}")

        # Set remote_tls_validation if defined in config
        if "pulp" in CONFIG and "remote_tls_validation" in CONFIG["pulp"]:
            tls_validation = CONFIG["pulp"]["remote_tls_validation"]
        for domain in CONFIG["pulp"]["internal_domains"].split(","):
            if domain in url:
                ca_cert = self._root_ca
                tls_validation = True
                break

        remote_config = {
            "name": name,
            "url": url,
            "ca_cert": ca_cert,
            "client_cert": client_cert,
            "client_key": client_key,
            "username": username,
            "password": password,
            "sock_connect_timeout": float(CONFIG["remotes"]["sock_connect_timeout"]),
            "sock_read_timeout": float(CONFIG["remotes"]["sock_read_timeout"]),
            "policy": "immediate",
            "tls_validation": tls_validation,
            "proxy_url": proxy_url,
        }

        if remote_type == "deb":
            remote_config.update(
                {
                    "distributions": distributions,
                    "components": components,
                    "architectures": architectures,
                    "ignore_missing_package_indices": ignore_missing_package_indices,
                }
            )

        remote_class = get_remote_class(remote_type)
        pulp_remote = remote_class(**remote_config)

        new_remote(self._pulp_client, pulp_remote)
        return pulp_remote

    # pylint:disable=unused-argument
    def update_remote(
        self,
        pulp_remote: Remote,
        url: str,
        ca_cert: str = None,
        client_cert: str = None,
        client_key: str = None,
        username: str = None,
        password: str = None,
        proxy_url: str = None,
        tls_validation: bool = False,
        distributions: str = None,
        components: str = None,
        architectures: str = None,
        ignore_missing_package_indices: bool = False,
    ):
        """Creates a new remote on the pulp server. If the URL to sync from is not an internal
        URL then tls validation is disabled. This is because at GR synching through the bluecoat
        proxy goes via http, even for https addresses. If tls validation is turned on the repo
        sync will end up failing.

        :param pulp_remote: pulp remote to update
        :type pulp_remote: pulp3.resources.Remote
        :param url: url to download repo from
        :type url: str
        :param ca_cert: A PEM encoded CA certificate used to validate the server certificate
                        presented by the remote server. This is needed for RHEL as there is no
                        SSL interception on the proxy for RedHat otherwise it breaks auth
        :type ca_cert: str
        :param client_cert: A PEM encoded client certificate used for authentication. This is
                            needed when synching repos from RedHat
        :type client_cert: str
        :param client_key: A PEM encoded private key used for authentication. This is needed
                           when synching repos from RedHat
        :type client_key: str
                :param username: username to use for authentication when synching.
                :type username: str
                :param password: authentication password to use when syncing.
                :type password: str
        :param proxy_url: The proxy URL. Format: scheme://host:port
        :type proxy_url: str
        :param tls_validation: Perform TLS peer validation. Required for redhat
        :type tls_validation: bool
        :param distributions: Only valid for deb repositories, if provided for any other
                              repo type the option is ignored. Whitespace separated list of
                              distributions to sync. The distribution is the path from the
                              repository root to the "Release" file you want to access. This is
                              often, but not always, equal to either the codename or the suite of
                              the release you want to sync. If the repository you are trying to sync
                              uses "flat repository format", the distribution must end with a "/".
                              Based on "/etc/apt/sources.list" syntax.
        :type distributions: str
        :param components: Only valid for deb repositories, if provided for any other
                           repo type the option is ignored. Whitespace separatet list of components
                           to sync. If none are supplied, all that are available will be
                           synchronized. Leave blank for repositores using "flat repository
                           format".
        :type components: str
        :param architectures: Only valid for deb repositories, if provided for any other
                              repo type the option is ignored. Whitespace separated list of
                              architectures to sync If none are supplied, all that are available
                              will be synchronized. A list of valid architecture specification
                              strings can be found by running "dpkg-architecture -L". A sync will
                              download the intersection of the list of architectures provided via
                              this field and those provided by the relevant "Release" file.
                              Architecture="all" is always synchronized and does not need to be
                              provided here.
        :type architectures: str
        :param ignore_missing_package_indices: Only valid for deb repositories, By default, upstream
                                               repositories that declare architectures and
                                               corresponding package indices in their Release files
                                               without actually publishing them, will fail to
                                               synchronize. Set this flag to True to allow the
                                               synchronization of such "partial mirrors" instead.
                                               Alternatively, you could make your remote filter by
                                               architectures for which the upstream repository does
                                               have indices
        :type ignore_missing_package_indices: bool
        """

        log.debug(
            f"attempting to update remote {pulp_remote.pulp_href} with name {pulp_remote.name} "
            f"on {self._pulp_server.name}"
        )

        updates_needed = False

        for domain in CONFIG["pulp"]["internal_domains"].split(","):
            if domain in url:
                ca_cert = self._root_ca
                tls_validation = True
                break

        passed_arguments = locals()
        passed_arguments["sock_read_timeout"] = float(
            CONFIG["remotes"]["sock_read_timeout"]
        )
        passed_arguments["sock_connect_timeout"] = float(
            CONFIG["remotes"]["sock_connect_timeout"]
        )

        for arg_name, arg_value in passed_arguments.items():
            if hasattr(pulp_remote, arg_name):
                # Pulp sometimes adds a new line to certain args e.g. ca_cert, so strip out any
                # new lines that could cause a potential and uneeded update
                pulp_remote_arg_value = getattr(pulp_remote, arg_name)
                if isinstance(pulp_remote_arg_value, str):
                    pulp_remote_arg_value = pulp_remote_arg_value.strip()
                if isinstance(arg_value, str):
                    arg_value = arg_value.strip()

                if pulp_remote_arg_value != arg_value:
                    log.debug(
                        f"remote update needed for {arg_name}, "
                        f"{pulp_remote_arg_value} != {arg_value}"
                    )
                    setattr(pulp_remote, arg_name, arg_value)
                    updates_needed = True

        if updates_needed:
            log.debug(
                f"updating remote {pulp_remote.pulp_href} on {self._pulp_server.name}"
            )
            update_remote_monitor(
                self._pulp_client,
                pulp_remote,
                poll_interval_sec=self._poll_interval_sec,
                max_wait_count=self._max_wait_count,
            )
        else:
            log.debug(
                f"no updates required for remote with href {pulp_remote.pulp_href} "
                f"on {self._pulp_server.name}"
            )

    def create_distribution(
        self, name: str, base_path: str, repo_href: str, distribution_type: str
    ):
        """Creates a distribution on the pulp server. It is linked to a repository so that
        the latest version of a repository is served

        :param name: name of the distribution to server
        :type name: str
        :param base_path: path repo should be server from e.g. el7-x86_64/ext-centos7
        :type base_path: str
        :param repo_href: href of repository to link the distribution to
        :type repo_href: str
        :param distribution_type: type of distribution to create
        :type distribution_type: str
        :return: pulp3.resources.Distribution
        """

        log.debug(
            f"attempting to create repo {name} on {self._pulp_server.name}")
        distribution_class = get_distribution_class(distribution_type)

        distribution_config = {
            "name": name,
            "base_path": base_path,
            "repository": repo_href,
        }
        pulp_distribution = distribution_class(**distribution_config)

        new_distribution_monitor(
            self._pulp_client,
            pulp_distribution,
            poll_interval_sec=self._poll_interval_sec,
            max_wait_count=self._max_wait_count,
        )
        return pulp_distribution

    def update_distribution(
        self, pulp_distribution: Distribution, base_path: str, repo_href: str = None
    ):
        """Updates the specified distribution on the pulp server

        :param pulp_distribution: distribution to update
        :type pulp_distribution: Distribution
        :param base_path: base url of the distribution to from the base path
        :type base_path: str
        :param repo_href: href of the repo that should be linked to the distribution.
                          Param is ignored when set to none, so there is no unlinking carried out
        :type repo_href: str
        """

        updates_needed = False

        if pulp_distribution.base_path != base_path:
            updates_needed = True
            pulp_distribution.base_path = base_path

        if repo_href and pulp_distribution.repository != repo_href:
            updates_needed = True
            pulp_distribution.repository = repo_href

        if updates_needed:
            log.debug(
                f"attempting to update distribution {pulp_distribution.pulp_href} "
                f"on {self._pulp_server.name}"
            )
            update_distribution_monitor(
                self._pulp_client,
                pulp_distribution,
                poll_interval_sec=self._poll_interval_sec,
                max_wait_count=self._max_wait_count,
            )
        else:
            log.debug(
                f"not updates needed  for {pulp_distribution.pulp_href} "
                f"on {self._pulp_server.name}"
            )

    # pylint: disable=too-many-branches,too-many-statements,unused-argument
    def create_or_update_repository(
        self,
        name: str,
        description: str,
        repo_type: str,
        url: str = None,
        username: str = None,
        password: str = None,
        proxy_url: str = None,
        tls_validation: bool = False,
        ca_cert: str = None,
        client_cert: str = None,
        client_key: str = None,
        distributions: str = None,
        components: str = None,
        architectures: str = None,
        ignore_missing_package_indices: bool = False,
    ):
        """Creates/updates a remote, repository and distribution. Remote is only created
        when url is set. Also updated the pulp manager database creating the Repo and
        PulpServerRepo entities where required. Returns the PulpServerEntity, contain
        repository, remote, and distribution information

        :param name: name of the repo to create of update
        :type name: str
        :param description: description of the repo, must contain base_url: followed by the path
                            to use for the base_url e.g. el7-x86_64
        :type description: str
        :param repo_type: type of repo to create. e.g. rpm
        :type repo_type: str
        :param url: url to download repo from
        :type url: str
                :param username: username to use for authentication when synching.
                :type username: str
                :param password: Authentification password to use when syncing.
                :type password: str
        :param proxy_url: The proxy URL. Format: scheme://host:port
        :type proxy_url: str
        :param tls_validation: If True, TLS peer validation must be performed. Required for redhat
        :type tls_validation: bool
        :param remote_type: type of remote to create, e.g. rpm
        :type remote_type: str
        :param ca_cert: A PEM encoded CA certificate used to validate the server certificate
                        presented by the remote server. This is needed for RHEL as there is no
                        SSL interception on the proxy for RedHat otherwise it breaks auth
        :type ca_cert: str
        :param client_cert: A PEM encoded client certificate used for authentication. This is
                            needed when synching repos from RedHat
        :type client_cert: str
        :param client_key: A PEM encoded private key used for authentication. This is needed
                           when synching repos from RedHat
        :type client_key: str
        :param distributions: Only valid for deb repositories, if provided for any other
                              repo type the option is ignored. Whitespace separated list of
                              distributions to sync. The distribution is the path from the
                              repository root to the "Release" file you want to access. This is
                              often, but not always, equal to either the codename or the suite of
                              the release you want to sync. If the repository you are trying to sync
                              uses "flat repository format", the distribution must end with a "/".
                              Based on "/etc/apt/sources.list" syntax.
        :type distributions: str
        :param components: Only valid for deb repositories, if provided for any other
                           repo type the option is ignored. Whitespace separatet list of components
                           to sync. If none are supplied, all that are available will be
                           synchronized. Leave blank for repositores using "flat repository
                           format".
        :type components: str
        :param architectures: Only valid for deb repositories, if provided for any other
                              repo type the option is ignored. Whitespace separated list of
                              architectures to sync If none are supplied, all that are available
                              will be synchronized. A list of valid architecture specification
                              strings can be found by running "dpkg-architecture -L". A sync will
                              download the intersection of the list of architectures provided via
                              this field and those provided by the relevant "Release" file.
                              Architecture="all" is always synchronized and does not need to be
                              provided here.
        :type architectures: str
        :param ignore_missing_package_indices: Only valid for deb repositories, By default, upstream
                                               repositories that declare architectures and
                                               corresponding package indices in their Release files
                                               without actually publishing them, will fail to
                                               synchronize. Set this flag to True to allow the
                                               synchronization of such "partial mirrors" instead.
                                               Alternatively, you could make your remote filter by
                                               architectures for which the upstream repository does
                                               have indices
        :type ignore_missing_package_indices: bool
        :return: PulpServerRepo
        """

        if "base_url" not in description:
            raise PulpManagerValueError(
                f"Could not determine base_url for {name} from description"
            )

        pm_repo = self._get_or_create_pm_repo(name, repo_type)
        pm_pulp_server_repo = self._pulp_server_repo_crud.first(
            **{"pulp_server_id": self._pulp_server.id, "repo_id": pm_repo.id}
        )

        pulp_remote = None
        pulp_repo = None
        pulp_distribution = None
        base_url = description.split("base_url:")[1].strip()
        base_path = self._generate_base_path(name, base_url)

        if url:
            pulp_remote_search = get_all_remotes(
                self._pulp_client, repo_type, params={"name": name}
            )

            if len(pulp_remote_search) == 0:
                pulp_remote = self.create_remote(
                    name,
                    url,
                    repo_type,
                    ca_cert,
                    client_cert,
                    client_key,
                    username,
                    password,
                    proxy_url,
                    tls_validation,
                    distributions,
                    components,
                    architectures,
                    ignore_missing_package_indices,
                )
            else:
                pulp_remote = pulp_remote_search[0]
                self.update_remote(
                    pulp_remote,
                    url,
                    ca_cert,
                    client_cert,
                    client_key,
                    username,
                    password,
                    proxy_url,
                    tls_validation,
                    distributions,
                    components,
                    architectures,
                    ignore_missing_package_indices,
                )

        pulp_repo_search = get_all_repos(
            self._pulp_client, repo_type, params={"name": name}
        )

        if len(pulp_repo_search) == 0:
            pulp_repo = self.create_repo(
                name,
                description,
                repo_type,
                remote_href=pulp_remote.pulp_href if pulp_remote else None,
            )
        else:
            pulp_repo = pulp_repo_search[0]
            self.update_repo(
                pulp_repo,
                description,
                remote_href=pulp_remote.pulp_href if pulp_remote else None,
            )

        pulp_distribution_search = get_all_distributions(
            self._pulp_client, repo_type, params={"name": name}
        )

        if len(pulp_distribution_search) == 0:
            pulp_distribution = self.create_distribution(
                name, base_path, pulp_repo.pulp_href, repo_type
            )
        else:
            pulp_distribution = pulp_distribution_search[0]
            self.update_distribution(
                pulp_distribution, base_path, pulp_repo.pulp_href)

        try:
            if pm_pulp_server_repo:
                pm_update_config = {}

                if pm_pulp_server_repo.repo_href != pulp_repo.pulp_href:
                    pm_update_config["repo_href"] = pulp_repo.pulp_href
                if (
                    pulp_remote
                    and pm_pulp_server_repo.remote_href != pulp_remote.pulp_href
                ):
                    pm_update_config["remote_href"] = pulp_remote.pulp_href
                    pm_update_config["remote_feed"] = url
                if pm_pulp_server_repo.distribution_href != pulp_distribution.pulp_href:
                    pm_update_config["distribution_href"] = pulp_distribution.pulp_href

                if len(pm_update_config) > 0:
                    log.debug(
                        f"updating PulpServerRepo {pm_repo.id} in database")
                    self._pulp_server_repo_crud.update(
                        pm_pulp_server_repo, **pm_update_config
                    )
                    self._db.commit()
            else:
                log.debug(f"add PulpServerRepo {name} for {self._pulp_server}")
                pm_pulp_server_repo = self._pulp_server_repo_crud.add(
                    **{
                        "pulp_server_id": self._pulp_server.id,
                        "repo_id": pm_repo.id,
                        "repo_href": pulp_repo.pulp_href,
                        "remote_href": pulp_remote.pulp_href if pulp_remote else None,
                        "remote_feed": pulp_remote.url if pulp_remote else None,
                        "distribution_href": pulp_distribution.pulp_href,
                    }
                )
                self._db.commit()
        except exc.SQLAlchemyError:
            log.error("error occured adding/updating PulpServerRepo")
            log.error(traceback.format_exc())
            self._db.rollback()
            raise

        return pm_pulp_server_repo

    def _filter_pulp_objects(
        self, pulp_objects: List, regex_include: str = None, regex_exclude: str = None
    ):
        """Given a list of pulp object, filters on the name attribute using the provided regexs.
        Returns a dict where the key is the name of the pulp object and value is the
        pulp3.resource object

        :param pulp_objects: List of pulp objects to filter on
        :type pulp_objects: List
        :param regex_include: regex of entities to include
        :type regex_include: str
        :param regex_exclude: regex of entities that should be excluded. If there are entities
                              that match both include and exclude, then exclude takes precedence
                              and the entity will be excluded from the results
        :type regex_exclude: str
        :return: dict
        """

        entities = {}

        for entity in pulp_objects:
            # pylint: disable=no-else-continue
            if regex_exclude and re.search(regex_exclude, entity.name):
                continue
            elif regex_include and not re.search(regex_include, entity.name):
                continue
            elif regex_include and re.search(regex_include, entity.name):
                entities[entity.name] = entity
            else:
                entities[entity.name] = entity

        return entities

    def _get_remotes(
        self, client: Pulp3Client, regex_include: str = None, regex_exclude: str = None
    ):
        """Retrieves the remotes that exist on the pulp server and returns a dict where
        the key is the name of the remote and the value is a Remote object
        :param client: Pulp3Client to interact with pulp API
        :type client: Pulp3Client
        :param regex_include: regex of remotes to include in the results
        :type regex_include: str
        :param regex_exclude: regex of remotes that should be excluded. If there are remotes
                              that match both include and exclude, then exclude takes precedence
                              and the repo will be omitted from the results
        :type regex_exclude: str
        :return: dict
        """

        remotes = []

        for repo_type in SUPPORTED_REPO_TYPES:
            remotes = remotes + get_all_remotes(client, repo_type)

        return self._filter_pulp_objects(remotes, regex_include, regex_exclude)

    def _get_repositories(
        self, client: Pulp3Client, regex_include: str = None, regex_exclude: str = None
    ):
        """Retrieves the repositories that exist on the pulp server and returns a dict where
        the key is the name of the repository and the value is a Repository object
        :param client: Pulp3Client to interact with pulp API
        :type client: Pulp3Client
        :param regex_include: regex of repos to include in the results
        :type regex_include: str
        :param regex_exclude: regex of repos that should be excluded. If there are repos that match
                              both include and exclude, then exclude takes precedence and the repo
                              will be omitted from the results
        :type regex_exclude: str
        :return: dict
        """

        repositories = []

        for repo_type in SUPPORTED_REPO_TYPES:
            repositories = repositories + get_all_repos(client, repo_type)

        return self._filter_pulp_objects(repositories, regex_include, regex_exclude)

    def _get_distributions(
        self, client: Pulp3Client, regex_include: str = None, regex_exclude: str = None
    ):
        """Retrieves the distributions that exist on the pulp server and returns a dict where
        the key is the name of the distribution and the value is a Distribution object
        :param client: Pulp3Client to interact with pulp API
        :type client: Pulp3Client
        :param regex_include: regex of repos that exist on the source that should be set up
                              for synching on the target
        :type regex_include: str
        :param regex_exclude: regex of repos that should be excluded from copying to the target.
                              If there are repos that match both include and exclude, then exclude
                              takes precedence and the repo will not be setup on the target
        :type regex_exclude: str
        :return: dict
        """

        distributions = []

        for repo_type in SUPPORTED_REPO_TYPES:
            distributions = distributions + \
                get_all_distributions(client, repo_type)

        return self._filter_pulp_objects(distributions, regex_include, regex_exclude)

    def _generate_feed_from_distribution(
        self, pulp_server_name: str, distribution: Distribution
    ):
        """Generates a URL for a repo from a distribution that exists on a pulp server

        :param pulp_server_name: fqdn of the pulp server the distribution exists on
        :type pulp_server_name: str
        :param distribution: distribution to generate the feed from
        :type distribution: Distribution
        :return: str
        """

        protocol = "https"
        if "pulp" in CONFIG and "use_https_for_sync" in CONFIG["pulp"]:
            use_https = CONFIG["pulp"]["use_https_for_sync"]
            if isinstance(use_https, str):
                use_https = use_https.lower() == "true"
            protocol = "https" if use_https else "http"
        
        return f"{protocol}://{pulp_server_name}/pulp/content/{distribution.base_path}"

    def _get_repo_file_list_from_url(self, url: str):
        """Returns a list of files/directories that exist at the given url. When pulp is hosting
        repo contents, when browsing it renders a basic web page, with a series of a tags
        for downloading files or browsing directories further. Directories . and .. are ignored

        :param url: url to get list of content from
        :type url: str
        :return: list
        """

        if not url.endswith("/"):
            url += "/"

        retry_count = 0

        while True:
            result = requests.get(url, timeout=60)

            # pylint: disable=no-else-return
            if result.status_code == 404:
                error = f"could not curl {url}, got 404. Repo sync maybe failed on primary"
                log.error(error)
                raise PulpManagerError(error)

            if result.status_code not in [200]:
                if retry_count == 3:
                    error = f"could not curl {url}, status code {result.status_code}"
                    log.error(error)
                    raise PulpManagerError(error)
            else:
                break

            retry_count += 1

        # Test will be similar to
        # <a href="../">../</a>\n<a href="focal-backports/">focal-backports/</a>
        # don't want ../
        return re.findall('<a href=\\"([A-z0-9-_]+)\\/?\\">', result.text)

    def _get_apt_distributions_from_url(self, url: str):
        """For synching deb remotes a list of distributions needs to be provided. The distributions
        that have been synched can be found under the /dists folder of a repo. Given the URL of the
        primary we are synching from we can curl the address and get the list of addresses that
        are required.

        :param url: url to calculate list of distributions to sync from
        :type url: str
        :return: list
        """

        # url will look similar to
        # http://pulp3mast1.example.com:24816/pulp/content/ubuntu-20.04-x86_64/focal-backports/
        # need to strip 24816 from this. In the future we that logic can be removed if can make
        # pulp not include this
        
        # Only convert to HTTPS if configured to do so
        use_https_for_sync = True  # Default to HTTPS
        if "pulp" in CONFIG and "use_https_for_sync" in CONFIG["pulp"]:
            use_https = CONFIG["pulp"]["use_https_for_sync"]
            if isinstance(use_https, str):
                use_https = use_https.lower() == "true"
            use_https_for_sync = use_https
        
        if use_https_for_sync:
            url = url.replace("http://", "https://")
        url = url.replace(":24816", "")

        if "dists/" not in url:
            url = url.rstrip('/') + "/dists/"

        # This check is due to _get_apt_distributions_from_url strips off the trailing
        # / when getting the list of possible distributions, when called recursivley,
        # need to make sure that the / is added back on otherwise for a hp repo
        # generated url would be
        # https://pulp/.../snap-2024-03-r1-ext-jammy-hpe-stk/dists/focalcurrent/
        # instead of https://pulp/.../snap-2024-03-r1-ext-jammy-hpe-stk/dists/focal/current/
        if not url.endswith("/"):
            url = url + "/"

        distributions = []
        potential_distributions = self._get_repo_file_list_from_url(url)

        if len(potential_distributions) == 0:
            return []

        for dist in potential_distributions:
            distribution_url = f"{url}{dist}"
            distribution_content = self._get_repo_file_list_from_url(
                distribution_url)
            if (
                "Release" not in distribution_content
                and "Release.gpg" not in distribution_content
            ):
                child_distributions = self._get_apt_distributions_from_url(
                    distribution_url
                )
                for child in child_distributions:
                    distributions.append(f"{dist}/{child}")
            else:
                distributions.append(dist)

        return distributions

    def _create_or_update_repository_source_pulp_server(
        self,
        source_repo: Repository,
        source_distribution: Distribution,
        source_name: str,
    ):
        """Creates or updates the remote, repository and distribution on the target, from the
        distribution and remote if it exists from the source.

        :param remote: If there is a remote that goes with the distribution on the source, then
                       the remote is used for the target to add any extra information that is
                       needed so the repo can be synched. For example with a deb distributions
                       and architectures maybe set as properties, to limit what is synched
        :type remote: Remote
        :param repo: Repository object that exists on source pulp server. This is used to generate
                     the config for creating the repository on the target pulp server
        :type repo: Repository
        :param distribution: distribution to use to create the repository on
        :type distribution: Distribution
        :param source_name: fqdn of the source pulp server
        :type source_name: str
        :return: bool
        """

        distributions = None
        url = self._generate_feed_from_distribution(
            source_name, source_distribution)

        if isinstance(source_repo, DebRepository):
            distributions = self._get_apt_distributions_from_url(
                source_distribution.base_url
            )
            if len(distributions) == 0:
#                raise PulpManagerValueError(
#                    f"No distributions found for deb {source_repo.name}"
#                )
                log.error(f"ERROR: No distributions found for deb {source_repo.name}")
                return 
                
        if isinstance(source_repo, FileRepository):
            url += "/PULP_MANIFEST"

        log.debug(f"create/update repo source {source_repo.name} URL {url}")

        self.create_or_update_repository(
            name=source_repo.name,
            description=source_repo.description,
            repo_type=get_repo_type_from_href(source_repo.pulp_href),
            url=url,
            distributions=" ".join(distributions) if distributions else None,
        )

    def find_repo_version_package_content(
        self,
        repo_version_href: str,
        name: str = None,
        version: str = None,
        sha256: str = None,
    ):
        """Searches the given repo version, for package content from the given paramters.
        At least one of name, version or sha256 must be given. Only fields returned from the
        package content are pulp_href, name/package, pkgId, sha256 and version

        :param repo_version_href: Repo version href to search the content of
        :type repo_version_href: str
        :param name: name of the package to find
        :type name: str
        :param version: version number of package to find
        :type version: str
        :param sha256 sum of the package
        :type sha256: str
        :return: List[Dict]
        """

        if not name and not version and not sha256:
            raise PulpManagerValueError(
                "name, version or sha256 must be specified")

        # not all fields are available on all repo types so pulp just omits those fields
        # from the results
        params = {
            "repository_version": repo_version_href,
            "fields": ["package", "pkgId", "name", "sha256", "pulp_href", "version"],
        }

        repo_type = get_repo_type_from_href(repo_version_href)
        # on deb repos the package name is referred to as package instead of name
        if name:
            if repo_type == "deb":
                params["package"] = name
            else:
                params["name"] = name
        if version:
            params["version"] = version
        if sha256:
            params["sha256"] = sha256

        return self._pulp_client.get_page_results(
            f"/pulp/api/v3/content/{repo_type}/packages/", params
        )

    def find_repo_package_content(
        self, repo_href: str, name: str = None, version: str = None, sha256: str = None
    ):
        """Searches the given repo, for package content from the given paramters. The latest
        repo version of the package is searched. At least one of name, version or sha256 must
        be given. Only fields returned from the package content are pulp_href, name/package,
        pkgId, sha256 and version

        :param repo_href: Repo href to search latest repo version of
        :type repo_href: str
        :param name: name of the package to find
        :type name: str
        :param version: version number of package to find
        :type version: str
        :param sha256 sum of the package
        :type sha256: str
        :return: List[Dict]
        """

        repo = get_repo(self._pulp_client, repo_href)
        return self.find_repo_version_package_content(
            repo.latest_version_href, name, version, sha256
        )

    def add_repos_from_pulp_server(
        self, source: str, regex_include: str, regex_exclude: str
    ):
        """Adds repos that exist on the source pulp server to the target
        :param source: fqdn of pulp instance where the repos will be synched from for the target
        :type source: str
        :param regex_include: regex of repos that exist on the source that should be set up
                              for synching on the target
        :type regex_include: str
        :param regex_exclude: regex of repos that should be excluded from copying to the target.
                              If there are repos that match both include and exclude, then exclude
                              takes precedence and the repo will not be setup on the target
        :type regex_exclude: str
        """

        if self._pulp_server.name == source:
            raise PulpManagerValueError(
                "value of source cannot be the same as the pulp server this service is managing"
            )

        source_pulp = self._pulp_server_crud.first(**{"name": source})

        if source_pulp is None:
            error = f"pulp server {source} not knwon to pulp manager"
            log.error(error)
            raise PulpManagerEntityNotFoundError(error)

        source_client = new_pulp_client(source_pulp)

        # Source remotes are needed mainly for debs so that the right options around components
        # and architectures are specified
        log.info(f"retrieving source repositories on {source_pulp.name}")
        source_repos = self._get_repositories(
            source_client, regex_include, regex_exclude
        )
        log.info(f"retrieving source distributions on {source_pulp.name}")
        source_distributions = self._get_distributions(
            source_client, regex_include, regex_exclude
        )

        for distribution_name, source_distribution in source_distributions.items():
            self._create_or_update_repository_source_pulp_server(
                source_repo=source_repos[distribution_name],
                source_distribution=source_distribution,
                source_name=source,
            )

        # Force a reoload of the pulp server, causing the SQL alchemy query
        # to re run. This will also update any other object refernces that
        # may have been retrieved from the db in other classes
        self._db.refresh(self._pulp_server)
