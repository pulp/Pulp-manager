"""Tests for the PulpReconciler service
"""

import pytest
from mock import patch

from pulp3_bindings.pulp3 import Pulp3Client
from pulp3_bindings.pulp3.resources import Repository, Remote, Distribution

from pulp_manager.app.database import session, engine
from pulp_manager.app.services.reconciler import PulpReconciler, PulpRepoInstance
from pulp_manager.app.models import PulpServer, Repo
from pulp_manager.app.repositories import (
    PulpServerRepository, PulpServerRepoRepository, RepoRepository
)

class TestPulpReconciler:
    """Tests the pulp reconciler to make sure appropriate DB changes would be made
    """

    @classmethod
    def setup_class(cls):
        """Add some additional sample data to be used for tests
        """

        db = session()
        pulp_server_repostiory = PulpServerRepository(db)
        pulp_server_repo_repository = PulpServerRepoRepository(db)
        repo_repository = RepoRepository(db)

        pulp_server = pulp_server_repostiory.add(**{
            "name": "reconciler-pulp.domain.local",
            "username": "username",
            "vault_service_account_mount": "service-accounts"
        })

        rpm_repo1 = repo_repository.add(**{
            "name": "rpm-repo-1",
            "repo_type": "rpm"
        })

        rpm_repo2 = repo_repository.add(**{
            "name": "rpm-repo-2",
            "repo_type": "rpm"
        })

        deb_repo1 = repo_repository.add(**{
            "name": "deb-repo-1",
            "repo_type": "deb"
        })

        deb_repo2 = repo_repository.add(**{
            "name": "deb-repo-2",
            "repo_type": "deb"
        })

        pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": rpm_repo1,
            "repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"
        })

        pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": rpm_repo2,
            "repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"
        })

        db.commit()

        cls.pulp_server_id = pulp_server.id
        cls.rpm_repo1_id = rpm_repo1.id
        cls.rpm_repo2_id = rpm_repo2.id
        cls.deb_repo1_id = deb_repo1.id
        cls.deb_repo2_id = deb_repo2.id

        db.close()
        engine.dispose()

    def setup_method(self, method):
        """Ensure an instance of PulpReconciler is available for all tests along with
        some fake data
        """

        self.db = session()
        self.pulp_server_repostiory = PulpServerRepository(self.db)
        self.pulp_server_repo_repository = PulpServerRepoRepository(self.db)
        self.repo_repository = RepoRepository(self.db)
        self.pulp_reconciler = PulpReconciler(self.db, "reconciler-pulp.domain.local")

    def teardown_method(self):
        """Ensure db connections are closed
        """

        self.db.close()
        engine.dispose()

    @patch("pulp_manager.app.services.reconciler.new_pulp_client")
    @patch("pulp_manager.app.services.reconciler.get_all_repos")
    @patch("pulp_manager.app.services.reconciler.get_all_remotes")
    @patch("pulp_manager.app.services.reconciler.get_all_distributions")
    def test_get_pulp_server_repo_instances(self, mock_get_all_distributions, mock_get_all_remotes,
            mock_get_all_repos, mock_new_pulp_client):
        """Tests that a dict of PulpRepoInstances is returned. Key is name of the repo
        and then the value if PulpRepoInstance with the correctly populated information
        """

        def new_pulp_client(pulp_server: PulpServer):
            return Pulp3Client(pulp_server.name, username=pulp_server.username, password="test")

        mock_new_pulp_client.side_effect = new_pulp_client

        mock_get_all_repos.return_value = [
            Repository(**{
                "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/1",
                "name": "rpm-repo1"
            }),
            Repository(**{
                "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/2",
                "name": "rpm-repo2"
            }),
            Repository(**{
                "pulp_href": "/pulp/api/v3/repositories/deb/apt/3",
                "name": "deb-repo1"
            })
        ]

        mock_get_all_remotes.return_value = [
            Remote(**{
                "pulp_href": "/pulp/api/v3/remotes/rpm/rpm/2",
                "name": "rpm-repo2",
                "url": "https://feed.domain.com",
                "policy": "immediate"
            })
        ]

        mock_get_all_distributions.return_value = [
            Distribution(**{
                "pulp_href": "/pulp/api/v3/distributions/rpm/rpm/2",
                "name": "rpm-repo2",
                "base_path": "rpm-repo2"
            }),
            Distribution(**{
                "pulp_href": "/pulp/api/v3/distributions/deb/apt/3",
                "name": "deb-repo1",
                "base_path": "deb-repo1"
            })
        ]

        result = self.pulp_reconciler._get_pulp_server_repo_instances()

        assert len(result) == 3

        assert "rpm-repo1" in result
        assert "rpm-repo2" in result
        assert "deb-repo1" in result

        rpm_repo1 = result["rpm-repo1"]
        assert rpm_repo1.name == "rpm-repo1"
        assert rpm_repo1.repo_href == "/pulp/api/v3/repositories/rpm/rpm/1"
        assert rpm_repo1.remote_href is None
        assert rpm_repo1.remote_feed is None
        assert rpm_repo1.distribution_href is None

        rpm_repo2 = result["rpm-repo2"]
        assert rpm_repo2.name == "rpm-repo2"
        assert rpm_repo2.repo_href == "/pulp/api/v3/repositories/rpm/rpm/2"
        assert rpm_repo2.remote_href == "/pulp/api/v3/remotes/rpm/rpm/2"
        assert rpm_repo2.remote_feed == "https://feed.domain.com"
        assert rpm_repo2.distribution_href == "/pulp/api/v3/distributions/rpm/rpm/2"

        deb_repo1 = result["deb-repo1"]
        assert deb_repo1.name == "deb-repo1"
        assert deb_repo1.repo_href == "/pulp/api/v3/repositories/deb/apt/3"
        assert deb_repo1.remote_href is None
        assert deb_repo1.remote_feed is None
        assert deb_repo1.distribution_href == "/pulp/api/v3/distributions/deb/apt/3"

    @patch("pulp_manager.app.services.reconciler.new_pulp_client")
    @patch("pulp_manager.app.services.reconciler.get_all_repos")
    @patch("pulp_manager.app.services.reconciler.get_all_remotes")
    @patch("pulp_manager.app.services.reconciler.get_all_distributions")
    def test_get_pulp_server_repo_instances_with_repo_remote_href(
            self, mock_get_all_distributions, mock_get_all_remotes,
            mock_get_all_repos, mock_new_pulp_client):
        """Tests that remotes are correctly linked when repo.remote href is set,
        even when the remote name differs from the repo name
        """

        def new_pulp_client(pulp_server: PulpServer):
            return Pulp3Client(pulp_server.name, username=pulp_server.username, password="test")

        mock_new_pulp_client.side_effect = new_pulp_client

        # Repo has remote attribute set to a remote with different name
        mock_get_all_repos.return_value = [
            Repository(**{
                "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/1",
                "name": "my-repo",
                "remote": "/pulp/api/v3/remotes/rpm/rpm/different-remote"
            })
        ]

        mock_get_all_remotes.return_value = [
            Remote(**{
                "pulp_href": "/pulp/api/v3/remotes/rpm/rpm/different-remote",
                "name": "some-other-name",
                "url": "https://href-matched-feed.domain.com",
                "policy": "immediate"
            })
        ]

        mock_get_all_distributions.return_value = []

        result = self.pulp_reconciler._get_pulp_server_repo_instances()

        assert len(result) == 1

        # Test href-based remote lookup (repo.remote set to different-named remote)
        my_repo = result["my-repo"]
        assert my_repo.name == "my-repo"
        assert my_repo.repo_href == "/pulp/api/v3/repositories/rpm/rpm/1"
        assert my_repo.remote_href == "/pulp/api/v3/remotes/rpm/rpm/different-remote"
        assert my_repo.remote_feed == "https://href-matched-feed.domain.com"

    def test_add_missing_repos(self):
        """Test that a list of repo instances get added to the db and a dict is returned
        containg the newly added entries
        """

        pulp_repos = {
            "test-repo-1": PulpRepoInstance(
                "test-repo-1",
                "/pulp/api/v3/repositories/deb/apt/123",
                None,
                None,
                None
            ),
            "test-repo-2": PulpRepoInstance(
                "test-repo-2",
                "/pulp/api/v3/repositories/rpm/rpm/245",
                None,
                None,
                None
            )
        }

        result = self.pulp_reconciler._add_missing_repos(pulp_repos)
        assert "test-repo-1" in result
        assert "test-repo-2" in result
        assert isinstance(result["test-repo-1"], Repo)
        assert result["test-repo-1"].id is not None
        assert result["test-repo-1"].name == "test-repo-1"
        assert result["test-repo-1"].repo_type == "deb"
        assert result["test-repo-2"].id is not None
        assert result["test-repo-2"].name == "test-repo-2"
        assert result["test-repo-2"].repo_type == "rpm"

    def test_calculate_repos_to_add(self):
        """Checks that the correct dict is returned in the list for the repo to add
        to the pulp server
        """
        repos = self.pulp_reconciler._add_missing_repos({})
        pulp_repo_instances = {
            "rpm-repo-1": PulpRepoInstance(
                "rpm-repo-1",
                "/pulp/api/v3/repositories/rpm/rpm/123",
                "/pulp/api/v3/remotes/rpm/rpm/123",
                "http://feed.domain.com",
                "/pulp/api/v3/distributions/rpm/rpm/123"
            ),
            "deb-repo-1": PulpRepoInstance(
                "deb-repo-1",
                "/pulp/api/v3/repositories/deb/apt/789",
                "/pulp/api/v3/remotes/deb/apt/789",
                "http://deb-feed.domain.com",
                "/pulp/api/v3/distributions/deb/apt/789"
            ),
        }

        result = self.pulp_reconciler._calculate_repos_to_add(repos, pulp_repo_instances)
        repo_config = result[0]
        assert len(result) == 1
        assert repo_config["pulp_server_id"] == self.pulp_server_id
        assert repo_config["repo_id"] == self.deb_repo1_id
        assert repo_config["repo_href"] == "/pulp/api/v3/repositories/deb/apt/789"
        assert repo_config["remote_href"] == "/pulp/api/v3/remotes/deb/apt/789"
        assert repo_config["remote_feed"] == "http://deb-feed.domain.com"
        assert repo_config["distribution_href"] == "/pulp/api/v3/distributions/deb/apt/789"

    def test_calculate_repos_to_update(self):
        """Checks that the correct dict is returned in the list for the repo to update
        for the pulp server
        """

        # From the sample data inserted in the class method only rpm-repo-1 and rpm-repo-2
        # are associated with the pulp server, so rpm-repo-1 should be the one that is updated

        pulp_repo_instances = {
            "rpm-repo-1": PulpRepoInstance(
                "rpm-repo-1",
                "/pulp/api/v3/repositories/rpm/rpm/123",
                "/pulp/api/v3/remotes/rpm/rpm/123",
                "http://feed.domain.com",
                "/pulp/api/v3/distributions/rpm/rpm/123"
            ),
            "deb-repo-1": PulpRepoInstance(
                "deb-repo-1",
                "/pulp/api/v3/repositories/deb/apt/789",
                "/pulp/api/v3/remotes/deb/apt/789",
                "http://deb-feed.domain.com",
                "/pulp/api/v3/distributions/deb/apt/789"
            ),
        }

        result = self.pulp_reconciler._calculate_repos_to_update(pulp_repo_instances)
        assert len(result) == 1
        repo_config = result[0]
        assert repo_config["id"] == self.rpm_repo1_id
        assert repo_config["remote_href"] == "/pulp/api/v3/remotes/rpm/rpm/123"
        assert repo_config["remote_feed"] == "http://feed.domain.com"
        assert repo_config["distribution_href"] == "/pulp/api/v3/distributions/rpm/rpm/123"

    def test_calculate_repos_to_delete(self):
        """Checks that the correct entitiy is returned in the list for the repo to delete
        for the pulp server
        """

        pulp_repo_instances = {
            "rpm-repo-1": PulpRepoInstance(
                "rpm-repo-1",
                "/pulp/api/v3/repositories/rpm/rpm/123",
                "/pulp/api/v3/remotes/rpm/rpm/123",
                "http://feed.domain.com",
                "/pulp/api/v3/distributions/rpm/rpm/123"
            ),
            "deb-repo-1": PulpRepoInstance(
                "deb-repo-1",
                "/pulp/api/v3/repositories/deb/apt/789",
                "/pulp/api/v3/remotes/deb/apt/789",
                "http://deb-feed.domain.com",
                "/pulp/api/v3/distributions/deb/apt/789"
            ),
        }

        result = self.pulp_reconciler._calculate_repos_to_delete(pulp_repo_instances)
        assert len(result) == 1
        assert result[0].repo.name == "rpm-repo-2"

    @patch("pulp_manager.app.services.reconciler.PulpReconciler._get_pulp_server_repo_instances")
    def test_reconcile(self, mock_get_pulp_server_repo_instances):
        """Tests that the changes to a pulp servers repos are appropriately made
        when reconcile is called on the object
        """

        mock_get_pulp_server_repo_instances.return_value = {
            "rpm-repo-1": PulpRepoInstance(
                "rpm-repo-1",
                "/pulp/api/v3/repositories/rpm/rpm/123",
                "/pulp/api/v3/remotes/rpm/rpm/123",
                "http://feed.domain.com",
                "/pulp/api/v3/distributions/rpm/rpm/123"
            ),
            "deb-repo-1": PulpRepoInstance(
                "deb-repo-1",
                "/pulp/api/v3/repositories/deb/apt/789",
                "/pulp/api/v3/remotes/deb/apt/789",
                "http://deb-feed.domain.com",
                "/pulp/api/v3/distributions/deb/apt/789"
            ),
        }

        updated_pulp_server = self.pulp_reconciler.reconcile()
        assert len(updated_pulp_server.repos) == 2
        for repo in updated_pulp_server.repos:
            assert repo.repo.name in ["rpm-repo-1", "deb-repo-1"]
