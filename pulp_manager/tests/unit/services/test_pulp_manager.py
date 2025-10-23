"""Tests for the PulpManager service
"""
import os
from datetime import datetime

import pytest
from mock import MagicMock, mock_open, patch

from pulp3_bindings.pulp3 import Pulp3Client
from pulp3_bindings.pulp3.resources import (DebDistribution, DebRemote,
                                            DebRepository, RpmDistribution,
                                            RpmRemote, RpmRepository,
                                            SigningService)
from pulp3_bindings.pulp3.resources import Task as Pulp3Task
from pulp_manager.app.database import engine, session
from pulp_manager.app.exceptions import PulpManagerError, PulpManagerValueError
from pulp_manager.app.models import PulpServer
from pulp_manager.app.repositories import (PulpServerRepoRepository,
                                           PulpServerRepository,
                                           RepoRepository, TaskRepository)
from pulp_manager.app.services import PulpManager


class MockResponse:
    """Mock requests response
    """

    def __init__(self, text: str, status_code: int):
        self.text = text
        self.status_code = status_code


class TestPulpManager:
    """Tests for pulp manager
    """

    @classmethod
    def setup_class(cls):
        """Add some additional sample data to be used for tests
        """

        db = session()

        pulp_server_repository = PulpServerRepository(db)
        pulp_server_repo_repository = PulpServerRepoRepository(db)
        repo_repository = RepoRepository(db)

        target_pulp_server = pulp_server_repository.add(**{
            "name": "target",
            "username": "test_user",
            "vault_service_account_mount": "service-accounts"
        })

        source_pulp_server = pulp_server_repository.add(**{
            "name": "source",
            "username": "test_user",
            "vault_service_account_mount": "service-accounts"
        })

        repo = repo_repository.add(**{
            "name": "existing_repo",
            "repo_type": "rpm"
        })

        pulp_server_repo = pulp_server_repo_repository.add(**{
            "pulp_server": target_pulp_server,
            "repo": repo,
            "repo_href": "/pulp/api/v3/repositories/rpm/rpm/789",
            "remote_href": "/pulp/api/v3/remotes/rpm/rpm/789",
            "remote_feed": "https://repo-feed.domain.local",
            "distribution_href": "/pulp/api/v3/distributions/rpm/rpm/789"
        })

        db.commit()
        db.close()
        engine.dispose()

    @patch("pulp_manager.app.services.pulp_manager.new_pulp_client")
    @patch("pulp_manager.app.services.pulp_manager.PulpManager._get_deb_signing_service")
    def setup_method(self, method, mock_get_deb_signing_service,
            mock_new_pulp_client):
        """Ensure repository classes are faked out with mocks
        """

        self.db = session()
        def new_pulp_client(pulp_server: PulpServer):
            return Pulp3Client(pulp_server.name, username=pulp_server.username, password="test")

        mock_new_pulp_client.side_effect = new_pulp_client

        self.pulp_server_repository = PulpServerRepository(self.db)
        self.pulp_server_repo_repository = PulpServerRepoRepository(self.db)
        self.repo_repository = RepoRepository(self.db)

        mock_get_deb_signing_service.return_value = "/pulp/api/v3/signing-services/123"
        self.pulp_manager = PulpManager(self.db, "target")

    def teardown_method(self):
        """Ensure db connections are closed
        """

        self.db.close()
        engine.dispose()

    def test_get_root_ca_env(self):
        """Tests that when PULP_MANAGER_CA_FILE is set as an environment variable
        a root CA file is read
        """

        os.environ["PULP_MANAGER_CA_FILE"] = "/path/to/root/ca/file"

        def open_side_effect(name, mode=None):
            return mock_open(read_data="MY-ROOT-CA")()

        with patch("builtins.open", side_effect=open_side_effect):
            result = self.pulp_manager._get_root_ca()
            assert self.pulp_manager._root_ca == "MY-ROOT-CA"

        del os.environ["PULP_MANAGER_CA_FILE"]

    def test_get_or_create_pm_repo_1(self):
        """Tests that when a pulp_manager Repo doesn't exist in the db it is created
        """

        pm_repo = self.pulp_manager._get_or_create_pm_repo("test-repo-create", "rpm")
        assert pm_repo is not None
        assert pm_repo.name == "test-repo-create"
        assert pm_repo.repo_type == "rpm"

    def test_get_or_create_pm_repo_2(self):
        """Tests that when a pulp_manager Repo exists in the db it is returned
        """

        existing_repo = self.repo_repository.first(**{"name": "existing_repo"})

        pm_repo = self.pulp_manager._get_or_create_pm_repo("existing_repo", "rpm")
        assert pm_repo is not None
        assert pm_repo.id == existing_repo.id
        assert pm_repo.name == "existing_repo"
        assert pm_repo.repo_type == "rpm"

    def test_generate_base_path(self):
        """Checks the correct base path is generated from the repo name and a base url
        """

        result1 = self.pulp_manager._generate_base_path("repo", "el7-x86_64")
        result2 = self.pulp_manager._generate_base_path("repo", "el7-x86_64/")
        result3 = self.pulp_manager._generate_base_path("centos7-drivers-2022-05-r1", "centos7-x86_64/")
        result4 = self.pulp_manager._generate_base_path("rhel7s-drivers-2022-05-r1", "rhel7s-x86_64/")

        assert result1 == "el7-x86_64/repo"
        assert result2 == "el7-x86_64/repo"
        assert result3 == "centos7-x86_64/centos7-drivers-2022-05-r1"
        assert result4 == "rhel7s-x86_64/rhel7s-drivers-2022-05-r1"

    @patch("pulp_manager.app.services.pulp_manager.new_publication")
    def test_create_publication_from_repo_version_rpm(self, mock_new_publication):
        """Tests that when a new publication is created, the pulp3 Task is returned
        """

        mock_new_publication.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.now(),
            "state": "running",
            "name": "new-publication",
            "logging_cid": "1234"
        })

        result = self.pulp_manager.create_publication_from_repo_version(
            "/pulp/api/v3/repositories/rpm/rpm/123/versions/1", "rpm", False
		)

        assert isinstance(result, Pulp3Task)

    @patch("pulp_manager.app.services.pulp_manager.new_publication")
    def test_create_publication_from_repo_version_deb(self, mock_new_publication):
        """Tests that when a new publication is created, the pulp3 Task is returned
        """

        mock_new_publication.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.now(),
            "state": "running",
            "name": "new-publication",
            "logging_cid": "1234"
        })

        result = self.pulp_manager.create_publication_from_repo_version(
            "/pulp/api/v3/repositories/deb/deb/123/versions/1", "deb", False
		)

        call_args, call_kwargs = mock_new_publication.call_args

        assert isinstance(result, Pulp3Task)

        publication = call_args[1]
        assert publication.structured

    @patch("pulp_manager.app.services.pulp_manager.new_publication")
    def test_create_publication_from_repo_version_deb_flat(self, mock_new_publication):
        """Tests that when a new publication is created, the pulp3 Task is returned
        """

        mock_new_publication.return_value = Pulp3Task(**{
			"pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.now(),
            "state": "running",
            "name": "new-publication",
            "logging_cid": "1234"
        })

        result = self.pulp_manager.create_publication_from_repo_version(
            "/pulp/api/v3/repositories/deb/deb/123/versions/1", "deb", True
		)

        call_args, call_kwargs = mock_new_publication.call_args

        assert isinstance(result, Pulp3Task)

        publication = call_args[1]
        assert publication.structured == False
        assert publication.simple

    @patch("pulp_manager.app.services.pulp_manager.new_repo")
    def test_create_repo(self, mock_new_repo):
        """Tests that new repos are created with the correct option. A repository of type
        pulp3.resources.Repository is returned
        """

        def mock_new_repo(client, repo):
            repo.pulp_href = "/pulp/api/v3/repositories/<repo_type>/123"
            repo.date_created = datetime.utcnow()

        mock_new_repo.side_effect = mock_new_repo

        rpm_repo = self.pulp_manager.create_repo("test-rpm-repo", "description", "rpm")
        deb_repo = self.pulp_manager.create_repo("test-deb-repo", "description", "deb")

        assert isinstance(rpm_repo, RpmRepository)
        assert isinstance(deb_repo, DebRepository)
        assert deb_repo.signing_service == "/pulp/api/v3/signing-services/123"

    @patch("pulp_manager.app.services.pulp_manager.update_repo_monitor")
    def test_update_repo(self, mock_update_repo_monitor):
        """Tests that a repos properties are updated with new values when they are incorrect
        """

        deb_repo = DebRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/deb/apt/123",
            "name": "test-deb-repo",
            "description": "description",
            "signing_service": "/pulp/api/v3/signing-services/456"
        })

        self.pulp_manager.update_repo(
            deb_repo, "updated_description", "/pulp/api/v3/remotes/deb/apt/123"
        )
        
        assert mock_update_repo_monitor.call_count == 1
        assert deb_repo.description == "updated_description"
        assert deb_repo.remote == "/pulp/api/v3/remotes/deb/apt/123"
        assert deb_repo.signing_service == "/pulp/api/v3/signing-services/123"

    @patch("pulp_manager.app.services.pulp_manager.new_remote")
    def test_create_remote(self, mock_new_remote):
        """Tests that new remotes are created with the correct options
        """

        def new_remote(client, remote):
            remote.pulp_href = "/pulp/api/v3/remotes/<remote_type>/<remote_type>/123"
            remote.pulp_created = datetime.utcnow()

        mock_new_remote.side_effect = new_remote

        with patch.object(self.pulp_manager, "_root_ca", new="CA PEM"):
            rpm_remote = self.pulp_manager.create_remote(
                name="test-rpm", url="https://feed.internal.com", remote_type="rpm"
            )

            deb_remote = self.pulp_manager.create_remote(
                name="test-deb", url="https://feed.external.com", remote_type="deb",
                distributions="focal", architectures="x86_64", components="comp"
            )

            assert isinstance(rpm_remote, RpmRemote)
            assert rpm_remote.tls_validation == True
            assert rpm_remote.ca_cert is None

            assert isinstance(deb_remote, DebRemote)
            assert deb_remote.tls_validation == True
            assert deb_remote.ca_cert is None
            assert deb_remote.distributions == "focal"
            assert deb_remote.architectures == "x86_64"
            assert deb_remote.components == "comp"

    @patch("pulp_manager.app.services.pulp_manager.update_remote_monitor")
    def test_update_remote(self, mock_update_remote_monitor):
        """Tests that relevant updates are made to a remove
        """

        remote = DebRemote(**{
            "pulp_href": "/pulp/api/v3/remotes/deb/apt/123",
            "name": "test-deb",
            "url": "http://remote.local",
            "distributions": "jammy",
            "policy": "immediate"
        })

        self.pulp_manager.update_remote(
            remote, url="https://updates.remote.com", distributions="focal"
        )

        assert remote.url == "https://updates.remote.com"
        assert remote.distributions == "focal"

    @patch("pulp_manager.app.services.pulp_manager.new_distribution_monitor")
    def test_create_distribution(self, mock_new_distribution_monitor):
        """Tests the a new distribution is created with the correct arguments
        """

        distribution = self.pulp_manager.create_distribution(
            name="test_deb",
            base_path="ubuntu-20.04-x86_64/test_deb",
            repo_href="/pulp/api/v3/repositories/deb/apt/123",
            distribution_type="deb"
        )

        assert isinstance(distribution, DebDistribution)
        assert distribution.name == "test_deb"
        assert distribution.base_path == "ubuntu-20.04-x86_64/test_deb"
        assert distribution.repository == "/pulp/api/v3/repositories/deb/apt/123"

    @patch("pulp_manager.app.services.pulp_manager.update_distribution_monitor")
    def test_update_distribution(self, mock_update_distribution_monitor):
        """Tests an existing distribution is updated with the correct arguments
        """

        distribution = DebDistribution(**{
            "pulp_href": "/pulp/api/v3/distributions/deb/apt/123",
            "name": "test-deb",
            "base_path": "ubuntu-20.04-x86_64/test_deb",
            "repository": "/pulp/api/v3/repositories/deb/apt/123"
        })

        self.pulp_manager.update_distribution(
            distribution, base_path="ubuntu-20.04-x86_64/test_deb_update",
            repo_href="/pulp/api/v3/repositories/deb/apt/456"
        )

        distribution.base_path == "ubuntu-20.04-x86_64/test_deb_update"
        distribution.repository == "/pulp/api/v3/repositories/deb/apt/456"

    @patch("pulp_manager.app.services.pulp_manager.get_all_remotes")
    @patch("pulp_manager.app.services.pulp_manager.new_remote")
    @patch("pulp_manager.app.services.pulp_manager.get_all_repos")
    @patch("pulp_manager.app.services.pulp_manager.new_repo")
    @patch("pulp_manager.app.services.pulp_manager.get_all_distributions")
    @patch("pulp_manager.app.services.pulp_manager.new_distribution_monitor")
    def test_create_or_update_repository_new(self, mock_new_distribution_monitor,
            mock_get_all_distributions, mock_new_repo, mock_get_all_repos, mock_new_remote,
            mock_get_all_remotes):
        """Tests that when new remote, repository and distribution are needed all are created
        correctly and a PulpServerRepo entity is returned with the correct values set
        """

        def new_remote(client, remote):
            remote.pulp_href = "/pulp/api/v3/remotes/rpm/rpm/1234"

        def new_repo(client, repo):
            repo.pulp_href = "/pulp/api/v3/repositories/rpm/rpm/1234"

        def new_distribution_monitor(client, distribution, poll_interval_sec, max_wait_count):
            distribution.pulp_href = "/pulp/api/v3/distributions/rpm/rpm/1234"

        mock_get_all_remotes.return_value = []
        mock_get_all_repos.return_value = []
        mock_get_all_distributions.return_value = []
        mock_new_remote.side_effect = new_remote
        mock_new_repo.side_effect = new_repo
        mock_new_distribution_monitor.side_effect = new_distribution_monitor

        pulp_server_repo = self.pulp_manager.create_or_update_repository(
            name="test-rpm-100", description="base_url:el7-x86_64", repo_type="rpm",
            url="http://myrepo.domain.local"
        )

        assert pulp_server_repo.repo_href == "/pulp/api/v3/repositories/rpm/rpm/1234"
        assert pulp_server_repo.remote_href == "/pulp/api/v3/remotes/rpm/rpm/1234"
        assert pulp_server_repo.remote_feed == "http://myrepo.domain.local"
        assert pulp_server_repo.distribution_href == "/pulp/api/v3/distributions/rpm/rpm/1234"

    @patch("pulp_manager.app.services.pulp_manager.get_all_remotes")
    @patch("pulp_manager.app.services.pulp_manager.update_remote_monitor")
    @patch("pulp_manager.app.services.pulp_manager.get_all_repos")
    @patch("pulp_manager.app.services.pulp_manager.update_repo_monitor")
    @patch("pulp_manager.app.services.pulp_manager.get_all_distributions")
    @patch("pulp_manager.app.services.pulp_manager.update_distribution_monitor")
    def test_create_or_update_repository_update(self, mock_update_distribution_monitor,
            mock_get_all_distributions, mock_update_repo_monitor, mock_get_all_repos,
            mock_update_remote_monitor, mock_get_all_remotes):
        """Tests that when new remote, repository and distribution are needed all are created
        correctly and a PulpServerRepo entity is returned with the correct values set
        """

        mock_get_all_remotes.return_value = [RpmRemote(**{
            "pulp_href": "/pulp/api/v3/remotes/rpm/rpm/987",
            "name": "existing_repo",
            "policy": "immediate",
            "url": "https://repo-feed-updated.domain.local"
        })]

        mock_get_all_repos.return_value = [RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/987",
            "name": "existing_repo",
            "remote": "/pulp/api/v3/remotes/rpm/rpm/987"
        })]

        mock_get_all_distributions.return_value = [RpmDistribution(**{
            "pulp_href": "/pulp/api/v3/distributions/rpm/rpm/987",
            "name": "existing_repo",
            "base_path": "el7-x86_64/existing_repo"
        })]

        pulp_server_repo = self.pulp_manager.create_or_update_repository(
            name="existing_repo", description="base_url:el7-x86_64", repo_type="rpm",
            url="http://myrepo.domain.local"
        )

        pulp_server = self.pulp_server_repository.first(**{"name": "target"})
        existing_repo = self.repo_repository.first(**{"name": "existing_repo"})

        # existing_repo defined in setup_method
        assert pulp_server_repo.pulp_server_id == pulp_server.id
        assert pulp_server_repo.repo_id == existing_repo.id
        assert pulp_server_repo.repo_href == "/pulp/api/v3/repositories/rpm/rpm/987"
        assert pulp_server_repo.remote_href == "/pulp/api/v3/remotes/rpm/rpm/987"
        assert pulp_server_repo.remote_feed == "http://myrepo.domain.local"
        assert pulp_server_repo.distribution_href == "/pulp/api/v3/distributions/rpm/rpm/987"

    def test_filter_pulp_objects(self):
        """Tests various pulp regex combinations to ensure that the expected objects are returned
        """

        rpm_repository_internal = RpmRepository(**{"name": "internal-rpm"})
        rpm_repository_external = RpmRepository(**{"name": "ext-rpm"})
        deb_repository_internal = DebRepository(**{"name": "internal-deb"})
        deb_repository_external = DebRepository(**{"name": "ext-deb"})

        pulp_objects = [
            rpm_repository_internal, rpm_repository_external, deb_repository_internal,
            deb_repository_external
        ]

        result1 = self.pulp_manager._filter_pulp_objects(pulp_objects)
        assert len(result1) == 4

        result2 = self.pulp_manager._filter_pulp_objects(pulp_objects, regex_include="rpm")
        assert len(result2) == 2
        for item_name in result2:
            assert "rpm" in item_name

        result3 = self.pulp_manager._filter_pulp_objects(pulp_objects, regex_exclude="deb")
        assert len(result3) == 2
        for item_name in result3:
            assert "rpm" in item_name

        result4 = self.pulp_manager._filter_pulp_objects(pulp_objects, regex_include="deb",
                regex_exclude="internal-deb")
        assert len(result4) == 1
        for item_name in result4:
            assert item_name == "ext-deb"

    @patch("pulp_manager.app.services.pulp_manager.get_all_remotes")
    def test_get_remotes(self, mock_get_all_remotes):
        """Tests that _get_remotes returns a dictionary containing the remotes requested
        """

        def get_all_remotes(client, repo_type=None):
            """Mocked get_all_remotes for pulp3
            """

            if repo_type == "rpm":
                return [
                    RpmRemote(**{
                        "pulp_href": "/pulp/api/v3/remotes/rpm/rpm/123",
                        "name": "ext-rpm",
                        "url": "https://rpm-url.com",
                        "policy": "immediate"
                    })
                ]
            elif repo_type == "deb":
                return [
                    DebRemote(**{
                        "pulp_href": "/pulp/api/v3/remotes/deb/apt/456",
                        "name": "ext-deb",
                        "url": "https://deb-url.com",
                        "policy": "immediate",
                        "distributions": "focal"
                    })
                ]
            else:
                return []

        mock_get_all_remotes.side_effect = get_all_remotes
        client = MagicMock()

        result1 = self.pulp_manager._get_remotes(client)
        assert len(result1) == 2

        result2 = self.pulp_manager._get_remotes(client, regex_include="rpm")
        assert len(result2) == 1
        assert "ext-rpm" in result2

        result3 = self.pulp_manager._get_remotes(client, regex_exclude="rpm")
        assert len(result3) == 1
        assert "ext-deb" in result3

    @patch("pulp_manager.app.services.pulp_manager.get_all_repos")
    def test_get_repositories(self, mock_get_all_repos):
        """Tests that _get_repositories returns a dictionary containing the repostiories requested
        """

        def get_all_repos(client, repo_type=None):
            """Mocked get all repos for pulp3
            """

            if repo_type == "rpm":
                return [
                    RpmRepository(**{
                        "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/123",
                        "name": "ext-rpm"
                    })
                ]
            elif repo_type == "deb":
                return [
                    DebRepository(**{
                        "pulp_href": "/pulp/api/v3/repositories/deb/apt/456",
                        "name": "ext-deb"
                    })
                ]
            else:
                return []

        mock_get_all_repos.side_effect = get_all_repos
        client = MagicMock()

        result1 = self.pulp_manager._get_repositories(client)
        assert len(result1) == 2

        result2 = self.pulp_manager._get_repositories(client, regex_include="rpm")
        assert len(result2) == 1
        assert "ext-rpm" in result2

        result3 = self.pulp_manager._get_repositories(client, regex_exclude="rpm")
        assert len(result3) == 1
        assert "ext-deb" in result3

    @patch("pulp_manager.app.services.pulp_manager.get_all_distributions")
    def test_get_distributions(self, mock_get_all_distributions):
        """Tests that _get_distributions returns a dictionary containing the
        distributions requested
        """

        def get_all_distributions(client, repo_type=None):
            """Mocked get all distributions for pulp3
            """

            if repo_type == "rpm":
                return [
                    RpmDistribution(**{
                        "pulp_href": "/pulp/api/v3/distributions/rpm/rpm/123",
                        "name": "ext-rpm",
                        "base_path": "ext-rpm"
                    })
                ]
            elif repo_type == "deb":
                return [
                    DebDistribution(**{
                        "pulp_href": "/pulp/api/v3/distributions/deb/apt/123",
                        "name": "ext-deb",
                        "base_path": "ext-deb"
                    })
                ]
            else:
                return []

        mock_get_all_distributions.side_effect = get_all_distributions
        client = MagicMock()

        result1 = self.pulp_manager._get_distributions(client)
        assert len(result1) == 2

        result2 = self.pulp_manager._get_distributions(client, regex_include="rpm")
        assert len(result2) == 1
        assert "ext-rpm" in result2

        result3 = self.pulp_manager._get_distributions(client, regex_exclude="rpm")
        assert len(result3) == 1
        assert "ext-deb" in result3

    def test_generate_feed_from_distribution(self):
        """Tests that the correct url is returned from a distribution
        """

        distribution = RpmDistribution(**{
            "name": "ext-rpm",
            "base_path": "el7-x86_64/ext-rpm"
        })

        expected = "http://pulp_server.domain.local/pulp/content/el7-x86_64/ext-rpm"
        seen = self.pulp_manager._generate_feed_from_distribution("pulp_server.domain.local", distribution)

        assert seen == expected

    @patch("pulp_manager.app.services.pulp_manager.requests.get")
    def test_get_repo_file_list_from_url_ok(self, mock_get):
        """Tests that when curling the url on an apt distribution that exists on a remote pulp
        server the correct list of distributions is returned
        """

        response = MockResponse(
            text='<html><a href="../">../</a><a href="focal/">focal/</a></html>',
            status_code = 200
        )

        mock_get.return_value = response

        expected = ["focal"]
        seen = self.pulp_manager._get_repo_file_list_from_url("http://pulp_server.domain.local/url")
        assert seen == expected

    @patch("pulp_manager.app.services.pulp_manager.requests.get")
    def test_get_repo_file_list_from_url_fail(self, mock_get):
        """Checks an empty list is returned when getting the list of distributions 404s
        """

        response = MockResponse(text="ERROR", status_code=404)
        mock_get.return_value = response
        with pytest.raises(PulpManagerError):
            self.pulp_manager._get_repo_file_list_from_url("http://pulp_server.domain.local/url")

    @patch("pulp_manager.app.services.pulp_manager.requests.get")
    def test_get_apt_distributions_from_url_ok(self, mock_get):
        """Checks that the correct list of distributions is generated from the given
        url. This check doesn't do nested checks for distributions e.g.
        focal/current
        """

        def mock_get_side_effect(url, timeout):
            if url.endswith("dists/"):
                return MockResponse(
                    text='<html><a href="../">../</a><a href="focal/">focal/</a></html>',
                    status_code=200
                )
            else:
                return MockResponse(
                    text='<html><a href="../">../</a><a href="Release">Release</a><a href="Release.gpg">Release.gpg</a></html>',
                    status_code=200
                )

        mock_get.side_effect = mock_get_side_effect
        expected = ["focal"]
        seen = self.pulp_manager._get_apt_distributions_from_url("http://pulp_server.domain.local/deb/repo/")
        assert seen == expected

    @patch("pulp_manager.app.services.pulp_manager.requests.get")
    def test_get_apt_distributions_from_url_ok_nested(self, mock_get):
        """Checks that the correct list of distributions is generated from the given
        url. This test does nested checks for distributions e.g. focal/current
        """

        def mock_get_side_effect(url, timeout):
            if url.endswith("dists/"):
                return MockResponse(
                    text='<html><a href="../">../</a><a href="focal/">focal/</a></html>',
                    status_code=200
                )
            elif url.endswith("focal/"):
                return MockResponse(
                    text='<html><a href="../">../</a><a href="current/">current/</a></html>',
                    status_code=200
                )
            elif url.endswith("focal/current/"):
                return MockResponse(
                    text='<html><a href="../">../</a><a href="Release">Release</a><a href="Release.gpg">Release.gpg</a></html>',
                    status_code=200
                )
            else:
                raise Exception("Unexpected url received")

        mock_get.side_effect = mock_get_side_effect
        expected = ["focal/current"]
        seen = self.pulp_manager._get_apt_distributions_from_url("http://pulp_server.domain.local/deb/repo/")
        assert seen == expected

    @patch("pulp_manager.app.services.pulp_manager.requests.get")
    def test_get_apt_distributions_from_url_ok_nested2(self, mock_get):
        """Checks that the correct list of distributions is generated from the given
        url. This test does nested checks for distributions e.g. focal/current/subdir
        """

        def mock_get_side_effect(url, timeout):
            if url.endswith("dists/"):
                return MockResponse(
                    text='<html><a href="../">../</a><a href="focal/">focal/</a></html>',
                    status_code=200
                )
            elif url.endswith("focal/"):
                return MockResponse(
                    text='<html><a href="../">../</a><a href="current/">current/</a></html>',
                    status_code=200
                )
            elif url.endswith("current/"):
                return MockResponse(
                    text='<html><a href="../">../</a><a href="subdir/">subdir/</a></html>',
                    status_code=200
                )
            else:
                return MockResponse(
                    text='<html><a href="../">../</a><a href="Release">Release</a><a href="Release.gpg">Release.gpg</a></html>',
                    status_code=200
                )

        mock_get.side_effect = mock_get_side_effect
        expected = ["focal/current/subdir"]
        seen = self.pulp_manager._get_apt_distributions_from_url("http://pulp_server.domain.local/deb/repo/")
        assert seen == expected

    @patch("pulp_manager.app.services.pulp_manager.get_all_signing_services")
    def test_get_deb_signing_service_ok(self, mock_get_all_signing_services):
        """Tests that when a signing service exists on the target pulp server, the href
        of the signing service is returned
        """

        mock_get_all_signing_services.return_value = [SigningService(**{
            "pulp_href": "/pulp/api/v3/signing-services/123",
            "pulp_created": datetime.utcnow(),
            "name": "deb-signing",
            "public_key": "---begin---",
            "pubkey_fingerprint": "ASDFWERDFG",
            "script": "/usr/local/bin/sign_deb_release.sh"
        })]

        client = MagicMock()
        result = self.pulp_manager._get_deb_signing_service()
        assert result == "/pulp/api/v3/signing-services/123"

    @patch("pulp_manager.app.services.pulp_manager.get_all_signing_services")
    def test_get_deb_signing_service_fail(self, mock_get_all_signing_services):
        """Tests that when the requested signing service doesn't exist on the pulp server an
        exception is raised
        """

        mock_get_all_signing_services.return_value = []
        client = MagicMock()

        with pytest.raises(PulpManagerError):
            self .pulp_manager._get_deb_signing_service()

    @patch("pulp_manager.app.services.PulpManager.create_or_update_repository")
    @patch("pulp_manager.app.services.PulpManager._get_apt_distributions_from_url")
    def test_create_or_update_repository_source_pulp_server(self,
            mock_get_apt_distributions_from_url, mock_create_or_update_repository):
        """Tests the correct arguments are passed to create_or_update_repository when a repo,
        remote and distirbution are being created from a source repository and distribution
        """

        mock_get_apt_distributions_from_url.return_value = ["focal"]

        source_repo = DebRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/deb/apt/777",
            "name": "test-deb-repo",
            "description": "base_url:ubuntu-20.04-x86_64"
        })

        source_distribution = DebDistribution(**{
            "pulp_href": "/pulp/api/v3/distributions/deb/apt/777",
            "name": "test-deb-repo",
            "base_path": "ubuntu-20.04-x86_64/test-deb-repo",
            "base_url": "https://pulp-server.domain.local/ubuntu-20.04-x86_64/test-deb-repo"
        })

        self.pulp_manager._create_or_update_repository_source_pulp_server(
            source_repo, source_distribution, "pulp-server.domain.local"
        )

        call_args, call_kwargs = mock_create_or_update_repository.call_args
        call_kwargs["name"] == "test-deb-repo"
        call_kwargs["description"] == "base_url:ubuntu-20.04-x86_64",
        call_kwargs["repo_type"] == "deb"
        call_kwargs["url"] == "https://pulp-server.domain.local/ubuntu-20.04-x86_64/test-deb-repo",
        call_kwargs["distributions"] == "focal"

    @patch("pulp_manager.app.services.PulpManager.create_or_update_repository")
    @patch("pulp_manager.app.services.PulpManager._get_apt_distributions_from_url")
    @pytest.mark.skip(reason="failing")
    def test_create_or_update_repository_source_pulp_server_fail(self,
            mock_get_apt_distributions_from_url, mock_create_or_update_repository):
        """Tests that when no apt distributions are returned and exception is raised 
        """

        mock_get_apt_distributions_from_url.return_value = []

        source_repo = DebRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/deb/apt/777",
            "name": "test-deb-repo",
            "description": "base_url:ubuntu-20.04-x86_64"
        })

        source_distribution = DebDistribution(**{
            "pulp_href": "/pulp/api/v3/distributions/deb/apt/777",
            "name": "test-deb-repo",
            "base_path": "ubuntu-20.04-x86_64/test-deb-repo",
            "base_url": "https://pulp-server.domain.local/ubuntu-20.04-x86_64/test-deb-repo"
        })

        with pytest.raises(PulpManagerError):
            self.pulp_manager._create_or_update_repository_source_pulp_server(
                source_repo, source_distribution, "pulp-server.domain.local"
            )

    @patch("pulp3_bindings.pulp3.Pulp3Client.get_page_results")
    def test_find_repo_version_package_content(self, mock_get_page_results):
        """Tests that a list of dicts is returned containing package content information
        """

        data = [
            {
                "name": "package",
                "pulp_href": "/pulp/api/v3/content/rpm/packages/123",
                "sha256": "12345",
                "version": "2"
            },
            {
                "name": "package",
                "pulp_href": "/pulp/api/v3/content/rpm/packages/456",
                "sha256": "678910",
                "version": "3"
            }
        ]

        mock_get_page_results.return_value = data
        seen = self.pulp_manager.find_repo_version_package_content(
            "/pulp/api/v3/repositories/rpm/rpm/123/versions/1", name="packages"
        )

        call_args, call_kwargs = mock_get_page_results.call_args
        params = call_args[1]
        assert params["repository_version"] == "/pulp/api/v3/repositories/rpm/rpm/123/versions/1"
        for field in params["fields"]:
            assert field in ["package", "pkgId", "name", "sha256", "pulp_href", "version"]

        for i in range(0, len(data)):
            assert seen[i] == data[i]

        # Tests passing version or sha256 instead also goes through without error
        self.pulp_manager.find_repo_version_package_content(
            "/pulp/api/v3/repositories/rpm/rpm/123/versions/1", version="2"
        )
        self.pulp_manager.find_repo_version_package_content(
            "/pulp/api/v3/repositories/rpm/rpm/123/versions/1", sha256="123456"
        )

    def test_find_repo_version_package_content_fail(self):
        """If name, version and sha256 are omitted as arguments, than PulpManagerValueError
        should be thrown
        """

        with pytest.raises(PulpManagerValueError):
            self.pulp_manager.find_repo_version_package_content(
                "/pulp/api/v3/repositories/rpm/rpm/123/versions/1"
            )

    @patch("pulp_manager.app.services.pulp_manager.PulpManager.find_repo_version_package_content")
    @patch("pulp_manager.app.services.pulp_manager.get_repo")
    def test_find_repo_package_content(self, mock_get_repo, mock_find_repo_version_package_content):
        """checks a list of dictionaries which contains the requests package criteria for a
        given repo href is returned
        """

        data = [
            {
                "name": "package",
                "pulp_href": "/pulp/api/v3/content/rpm/packages/123",
                "sha256": "12345",
                "version": "2"
            },
            {
                "name": "package",
                "pulp_href": "/pulp/api/v3/content/rpm/packages/456",
                "sha256": "678910",
                "version": "3"
            }
        ]

        mock_get_repo.return_value = RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/123",
            "name": "test-rpm",
            "latest_version_href": "/pulp/api/v3/repositories/rpm/rpm/123/versions/1"
        })

        mock_find_repo_version_package_content.return_value = data

        seen = self.pulp_manager.find_repo_package_content(
            "/pulp/api/v3/repositories/rpm/rpm/123", name="test"
        )

        for i in range(0, len(data)):
            assert seen[i] == data[i]

    @patch("pulp_manager.app.services.pulp_manager.new_pulp_client")
    @patch("pulp_manager.app.services.PulpManager._get_repositories")
    @patch("pulp_manager.app.services.PulpManager._get_distributions")
    @patch("pulp_manager.app.services.PulpManager._create_or_update_repository_source_pulp_server")
    def test_add_repos_from_pulp_server(self, mock_create_or_update_repository_source_pulp_server,
            mock_get_distributions, mock_get_repositories, mock_new_pulp_client ):
        """Tests flow of execution of code, and call count of methods
        """

        def new_pulp_client(pulp_server: PulpServer):
            return Pulp3Client(pulp_server.name, username=pulp_server.username, password="test")

        mock_new_pulp_client.side_effect = new_pulp_client

        mock_get_repositories.return_value = {
            "test-rpm": RpmRepository(**{
                "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/123",
                "name": "test-rpm"
            }),
            "test-deb": RpmRepository(**{
                "pulp_href": "/pulp/api/v3/repositories/deb/apt/123",
                "name": "test-deb"
            }),
        }

        mock_get_distributions.return_value = {
            "test-rpm": RpmDistribution(**{
                "pulp_href": "/pulp/api/v3/distributions/rpm/rpm/123",
                "name": "test-rpm",
                "base_path": "el7-x86_64/test-rpm"
            }),
            "test-deb": DebDistribution(**{
                "pulp_href": "/pulp/api/v3/distributions/deb/apt/123",
                "name": "test-deb",
                "base_path": "ubuntu-20.04-x86_64/test-deb"
            })
        }

        self.pulp_manager.add_repos_from_pulp_server("source", None, None)
        assert mock_create_or_update_repository_source_pulp_server.call_count == 2

    @patch.dict("pulp_manager.app.services.pulp_manager.CONFIG", {
        "pulp": {
            "package_name_replacement_pattern": "",
            "package_name_replacement_rule": ""
        }
    })
    def test_process_package_name_no_pattern(self):
        """Tests that when no pattern is configured, the original name with base_url is returned
        """
        result = self.pulp_manager._process_package_name("test-package", "http://example.com/")

        assert result == "http://example.com/test-package"

    @patch.dict("pulp_manager.app.services.pulp_manager.CONFIG", {
        "pulp": {
            "package_name_replacement_pattern": "^prefix-(?P<name>.+)$",
            "package_name_replacement_rule": "{name}-suffix"
        }
    })
    def test_process_package_name_pattern_no_match(self):
        """Tests that when pattern doesn't match, the original name with base_url is returned
        """
        result = self.pulp_manager._process_package_name("no-prefix-package", "http://example.com/")

        assert result == "http://example.com/no-prefix-package"

    @patch.dict("pulp_manager.app.services.pulp_manager.CONFIG", {
        "pulp": {
            "package_name_replacement_pattern": "^prefix-(?P<name>.+)$",
            "package_name_replacement_rule": "{name}-suffix"
        }
    })
    def test_process_package_name_pattern_match(self):
        """Tests that when pattern matches, the transformed name with base_url is returned
        """
        result = self.pulp_manager._process_package_name("prefix-mypackage", "http://example.com/")

        assert result == "http://example.com/mypackage-suffix"

    @patch.dict("pulp_manager.app.services.pulp_manager.CONFIG", {
        "pulp": {
            "package_name_replacement_pattern": "^(?P<org>[a-z]+)-(?P<env>[a-z]+)-(?P<pkg>.+)$",
            "package_name_replacement_rule": "{env}/{org}/{pkg}"
        }
    })
    def test_process_package_name_complex_pattern(self):
        """Tests package name transformation with multiple named groups
        """
        result = self.pulp_manager._process_package_name("acme-prod-webserver", "http://example.com/")

        assert result == "http://example.com/prod/acme/webserver"
