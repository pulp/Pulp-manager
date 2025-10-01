"""Tests for the RepoSyncher
"""

import json
import socket
from datetime import datetime, timedelta

import pytest
from mock import Mock, patch

from pulp3_bindings.pulp3 import Pulp3Client
from pulp3_bindings.pulp3.resources import (DebPublication, DebRepository,
                                            RpmPublication, RpmRemote,
                                            RpmRepository,
                                            RpmRepositoryVersion)
from pulp3_bindings.pulp3.resources import Task as Pulp3Task
from pulp3_bindings.pulp3.resources import Task as PulpTask
from pulp_manager.app.config import CONFIG
from pulp_manager.app.database import engine, session
from pulp_manager.app.models import (PulpServer, PulpServerRepo,
                                     PulpServerRepoTask, Repo, Task, TaskStage)
from pulp_manager.app.repositories import (PulpServerRepoRepository,
                                           PulpServerRepository,
                                           PulpServerRepoTaskRepository,
                                           RepoRepository, TaskRepository,
                                           TaskStageRepository)
from pulp_manager.app.services import RepoSyncher


class TestRepoSyncher:
    """Test class for RepoSyncher
    """

    @classmethod
    def setup_class(cls):
        """Add some additional sample data to be used for the tests
        """

        db = session()
        pulp_server_repository = PulpServerRepository(db)
        pulp_server_repo_repository = PulpServerRepoRepository(db)
        repo_repository = RepoRepository(db)

        cls.pulp_server = pulp_server_repository.add(**{
            "name": "pulp_server.domain.local",
            "username": "username",
            "vault_service_account_mount": "service-accounts"
        })

        cls.rpm_repo1 = repo_repository.add(**{"name": "rpm-repo1", "repo_type": "rpm"})
        cls.rpm_repo2 = repo_repository.add(**{"name": "rpm-repo2", "repo_type": "rpm"})
        cls.rpm_repo_ex = repo_repository.add(**{"name": "rpm-repo-ex", "repo_type": "rpm"})
        cls.deb_repo1 = repo_repository.add(**{"name": "deb-repo1", "repo_type": "deb"})
        cls.deb_repo2 = repo_repository.add(**{"name": "deb-repo2", "repo_type": "deb"})
        cls.deb_repo_ex = repo_repository.add(**{"name": "deb-repo-ex", "repo_type": "deb"})

        cls.pulp_server_rpm_repo1 = pulp_server_repo_repository.add(**{
            "pulp_server": cls.pulp_server,
            "repo": cls.rpm_repo1,
            "repo_href": "/pulp/api/v3/repositories/rpm/rpm/1",
            "remote_href": "/pulp/api/v3/remotes/rpm/rpm/1",
            "remote_feed": "https://feed.domain.local"
        })

        cls.pulp_server_rpm_repo2 = pulp_server_repo_repository.add(**{
            "pulp_server": cls.pulp_server,
            "repo": cls.rpm_repo2,
            "repo_href": "/pulp/api/v3/repositories/rpm/rpm/2"
        })

        cls.pulp_server_rpm_repo_ex = pulp_server_repo_repository.add(**{
            "pulp_server": cls.pulp_server,
            "repo": cls.rpm_repo_ex,
            "repo_href": "/pulp/api/v3/repositories/rpm/rpm/3",
            "remote_href": "/pulp/api/v3/remotes/rpm/rpm/3",
            "remote_feed": "https://feed.domain.local"
        })

        cls.pulp_server_deb_repo1 = pulp_server_repo_repository.add(**{
            "pulp_server": cls.pulp_server,
            "repo": cls.deb_repo1,
            "repo_href": "/pulp/api/v3/repositories/deb/apt/4",
            "remote_href": "/pulp/api/v3/remotes/deb/apt/4",
            "remote_feed": "https://feed.domain.local"
        })

        cls.pulp_server_deb_repo2 = pulp_server_repo_repository.add(**{
            "pulp_server": cls.pulp_server,
            "repo": cls.deb_repo2,
            "repo_href": "/pulp/api/v3/repositories/deb/apt/5"
        })

        cls.pulp_server_deb_repo_ex = pulp_server_repo_repository.add(**{
            "pulp_server": cls.pulp_server,
            "repo": cls.deb_repo_ex,
            "repo_href": "/pulp/api/v3/repositories/deb/apt/6",
            "remote_href": "/pulp/api/v3/remotes/deb/apt/6",
            "remote_feed": "https://feed.domain.local"
        })

        db.commit()
        db.close()
        engine.dispose()


    @patch("pulp_manager.app.services.repo_syncher.new_pulp_client")
    def setup_method(self, method, mock_new_pulp_client):
        """Ensure the repository classes are faked out with mocks
        """

        def new_pulp_client(pulp_server: PulpServer):
            return Pulp3Client(pulp_server.name, username=pulp_server.username, password="test")

        mock_new_pulp_client.side_effect = new_pulp_client

        self.db = session()
        self.pulp_server_repository = PulpServerRepository(self.db)
        self.pulp_server_repo_repository = PulpServerRepoRepository(self.db)
        self.repo_repository = RepoRepository(self.db)
        self.task_repository = TaskRepository(self.db)
        self.task_stage_repository = TaskStageRepository(self.db)
        self.pulp_server_repo_task_repository = PulpServerRepoTaskRepository(self.db)
        self.repo_syncher = RepoSyncher(self.db, "pulp_server.domain.local")

    def teardown_method(self):
        """Ensure db connections are closed
        """

        self.db.close()
        engine.dispose()

    def test_get_repos_to_sync_1(self):
        """Tests that given a PulpServer entity and regex_include/exclude tests that
        all repos that have remote_feed set are returned
        """

        repos_to_sync = self.repo_syncher._get_repos_to_sync()

        assert len(repos_to_sync) == 4
        for repo in repos_to_sync:
            # Access the repo name with the repo relationship on PulpServerRepo
            assert repo.repo.name in ["rpm-repo1", "rpm-repo-ex", "deb-repo1", "deb-repo-ex"]

    def test_get_repos_to_sync_2(self):
        """Tests that given a PulpServer with regex_include set, repos that match and have
        remote_feed set are returned
        """

        repos_to_sync = self.repo_syncher._get_repos_to_sync(regex_include="rpm-repo")
        assert len(repos_to_sync) == 2
        for repo in repos_to_sync:
            assert repo.repo.name in ["rpm-repo1", "rpm-repo-ex"]

    def test_get_repos_to_sync_3(self):
        """Tests that given a PulpServer with regex_exclude set, repos that match and have
        remote_feed are excluded, and those with a feed set that don't match are returned
        """

        repos_to_sync = self.repo_syncher._get_repos_to_sync(regex_exclude="-ex")
        assert len(repos_to_sync) == 2
        for repo in repos_to_sync:
            assert repo.repo.name in ["rpm-repo1", "deb-repo1"]

    def test_get_repos_to_sync_4(self):
        """Tests that give a PulpServer with regex_include and regex_exclude set, when a repo
        is matched both regexs ensures that the exclude regex taskes precedence and it is omitted
        """

        repos_to_sync = self.repo_syncher._get_repos_to_sync(
            regex_include="rpm", regex_exclude="-ex"
        )
        assert len(repos_to_sync) == 1
        assert repos_to_sync[0].repo.name == "rpm-repo1"

    def test_generate_tasks(self):
        """Tests that the correct number of tasks are generated for the givens repos
        """

        parent_task = self.task_repository.add(**{
            "name": "test generate tasks",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1"}
        })

        repos_to_sync = self.repo_syncher._get_repos_to_sync(regex_exclude="-ex")
        repo_tasks = self.repo_syncher._generate_tasks(
            self.repo_syncher._pulp_server.name, repos_to_sync, parent_task.id
        )

        assert len(repos_to_sync) > 0
        repo_ids_for_sync = [repo.id for repo in repos_to_sync]
        assert len(repos_to_sync) == len(repo_tasks)
        for task in repo_tasks:
            assert task.task_args["pulp_server_repo_id"] in repo_ids_for_sync

    @patch("pulp_manager.app.repositories.TaskRepository.bulk_add")
    def test_generate_tasks_fail(self, patched_bulk_add):
        """Tests that if any exceptions are raised during the generation of tasks
        it is caught where logging can take place and the re raised
        """

        parent_task = self.task_repository.add(**{
            "name": "test generate tasks fail",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1"}
        })

        repos_to_sync = self.repo_syncher._get_repos_to_sync(regex_exclude="-ex")
        assert len(repos_to_sync) > 0
        patched_bulk_add.side_effect = Mock(side_effect=Exception('Test'))

        with pytest.raises(Exception):
            self.repo_syncher._generate_tasks(self.pulp_server.name, repos_to_sync, parent_task.id)

    @patch("pulp_manager.app.services.repo_syncher.sync_repo")
    @patch("pulp_manager.app.services.repo_syncher.get_repo")
    def test_start_sync(self, mock_get_repo, mock_sync_repo):
        """Tests that when a sync is started and there are no errors, the task
        object gets a rep sync task associated with it
        """

        mock_sync_repo.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "running",
            "name": "repo-sync",
            "logging_cid": "123"
        })

        mock_get_repo.return_value = RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/1",
            "name": "rpm-repo1"
        })

        task = self.task_repository.add(**{
            "name": "test start sync",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/1"}
        })
        self.db.commit()
        task_stage = self.repo_syncher._start_sync(task, {})

        assert task.state == "running"
        assert task.date_started is not None
        assert "sync repo" in task_stage.name
        assert task_stage.task_id == task.id

    @patch("pulp_manager.app.services.repo_syncher.get_repo_version")
    @patch("pulp3_bindings.pulp3.Pulp3Client.get_page_results")
    def test_find_packages_to_remove_rpm(self, mock_get_page_results, mock_get_repo_version):
        """Checks that when package names in a repo version match the regex of banned packages
        then a list of hrefs for the banned packages is returned
        """

        mock_get_repo_version.return_value = RpmRepositoryVersion(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/123/versions/1/",
            "pulp_created": datetime.utcnow(),
            "number": 1,
            "repository": "/pulp/api/v3/repositories/rpm/rpm/123/",
            "content_summary": {
                "present": {
                    "rpm.package": {
                        "count": 2409,
                        "href": "/pulp/api/v3/content/rpm/packages/?repository_version_added=/pulp/api/v3/repositories/rpm/rpm/123/versions/1/"
                    }
                }
            }
        })

        # mock_get_page_results is directly calling the value of href of the package count
        # so a list of dicts will be being returned
        mock_get_page_results.return_value = [
            {"pulp_href": "/pulp/api/v3/content/rpm/packages/1", "name": "package1"},
            {"pulp_href": "/pulp/api/v3/content/rpm/packages/2", "name": "package2"},
            {"pulp_href": "/pulp/api/v3/content/rpm/packages/3", "name": "pp-skipfish-1"},
            {"pulp_href": "/pulp/api/v3/content/rpm/packages/4", "name": "pp-nmap"},
            {"pulp_href": "/pulp/api/v3/content/rpm/packages/5", "name": "sslstrip"},
            {"pulp_href": "/pulp/api/v3/content/rpm/packages/6", "name": "package6"}
        ]

        repo = RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/123/",
            "name": "test-repo"
        })


        CONFIG["pulp"]["banned_package_regex"] = "pp-skipfish-1|pp-nmap|sslstrip"
        result = self.repo_syncher._find_packages_to_remove(repo)
        assert len(result) == 3
        for href in result:
            assert href in [
                "/pulp/api/v3/content/rpm/packages/3",
                "/pulp/api/v3/content/rpm/packages/4",
                "/pulp/api/v3/content/rpm/packages/5"
            ]

    @patch("pulp_manager.app.services.repo_syncher.get_repo_version")
    @patch("pulp3_bindings.pulp3.Pulp3Client.get_page_results")
    def test_find_packages_to_remove_deb(self, mock_get_page_results, mock_get_repo_version):
        """Checks that when package names in a repo version match the regex of banned packages
        then a list of hrefs for the banned packages is returned
        """

        mock_get_repo_version.return_value = RpmRepositoryVersion(**{
            "pulp_href": "/pulp/api/v3/repositories/deb/apt/123/versions/1/",
            "pulp_created": datetime.utcnow(),
            "number": 1,
            "repository": "/pulp/api/v3/repositories/deb/apt/123/",
            "content_summary": {
                "present": {
                    "deb.package": {
                        "count": 2409,
                        "href": "/pulp/api/v3/content/deb/packages/?repository_version_added=/pulp/api/v3/repositories/deb/apt/123/versions/1/"
                    }
                }
            }
        })

        # mock_get_page_results is directly calling the value of href of the package count
        # so a list of dicts will be being returned
        mock_get_page_results.return_value = [
            {"pulp_href": "/pulp/api/v3/content/deb/packages/3", "name": "pp-skipfish-1"},
            {"pulp_href": "/pulp/api/v3/content/deb/packages/4", "name": "pp-nmap"},
            {"pulp_href": "/pulp/api/v3/content/deb/packages/5", "name": "sslstrip"},
        ]

        repo = DebRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/deb/apt/123/",
            "name": "test-repo"
        })

        result = self.repo_syncher._find_packages_to_remove(repo)
        assert len(result) == 3
        for href in result:
            assert href in [
                "/pulp/api/v3/content/deb/packages/3",
                "/pulp/api/v3/content/deb/packages/4",
                "/pulp/api/v3/content/deb/packages/5"
            ]

    @patch("pulp_manager.app.services.repo_syncher.get_repo")
    @patch("pulp_manager.app.services.repo_syncher.get_remote")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._find_packages_to_remove")
    @patch("pulp_manager.app.services.repo_syncher.modify_repo")
    def test_start_remove_banned_packages(self, mock_modify_repo, mock_find_packages_to_remove,
            mock_get_remote, mock_get_repo):
        """Tests that when there are packages to remove from a repo a modify repo task
        is kicked off and True is returned to indicate a task when started on the pulp server
        to modify the repo contents
        """

        mock_get_repo.return_value = RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/123",
            "name": "test-rpm",
            "latest_version_href": "/pulp/api/v3/repositories/rpm/rpm/123/versions/1"
        })

        mock_get_remote.return_value = RpmRemote(**{
            "pulp_href": "/pulp/api/v3/remotes/rpm/rpm/123",
            "name": "test-rpm",
            "url": "https://domain.local/",
            "policy": "immediate"
        })

        mock_find_packages_to_remove.return_value = ["/pulp/api/v3/content/rpm/packages/3"]
        mock_modify_repo.return_value = PulpTask(**{
            "pulp_href": "/pulp/api/v3/tasks/123/",
            "pulp_created": datetime.now(),
            "state": "running",
            "name": "modify-repo",
            "logging_cid": "1234"
        })

        task = self.task_repository.add(**{
            "name": "test removed banned packages",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })
        self.db.flush()

        result = self.repo_syncher._start_remove_banned_packages(task)
        assert result == True

        modify_repo_call_args, modify_repo_kwargs = mock_modify_repo.call_args
        assert modify_repo_call_args[1].pulp_href == "/pulp/api/v3/repositories/rpm/rpm/123"
        assert modify_repo_call_args[2] == "/pulp/api/v3/repositories/rpm/rpm/123/versions/1"
        assert modify_repo_kwargs["remove_content_units"] == ["/pulp/api/v3/content/rpm/packages/3"]

    @patch("pulp_manager.app.services.repo_syncher.get_repo")
    @patch("pulp_manager.app.services.repo_syncher.get_remote")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._find_packages_to_remove")
    def test_start_remove_banned_packages_skip1(self, mock_find_packages_to_remove,
            mock_get_remote, mock_get_repo):
        """Tests that if there are no pakcages to remove from a remote feed, False is returned
        inidcating the modiy repo step was never called
        """

        mock_get_repo.return_value = RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/123",
            "name": "test-rpm",
            "latest_version_href": "/pulp/api/v3/repositories/rpm/rpm/123/versions/1"
        })

        mock_get_remote.return_value = RpmRemote(**{
            "pulp_href": "/pulp/api/v3/remotes/rpm/rpm/123",
            "name": "test-rpm",
            "url": "https://domain.local/",
            "policy": "immediate"
        })

        task = self.task_repository.add(**{
            "name": "test removed banned packages skip1",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })
        self.db.flush()

        mock_find_packages_to_remove.return_value = []
        result = self.repo_syncher._start_remove_banned_packages(task)
        assert result == False

    @patch("pulp_manager.app.services.repo_syncher.get_repo")
    @patch("pulp_manager.app.services.repo_syncher.get_remote")
    def test_start_remove_banned_packages_skip2(self, mock_get_remote, mock_get_repo):
        """Tests that if the feed was from an internal domain then no checks are done for
        banned packages
        """

        mock_get_repo.return_value = RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/123",
            "name": "test-rpm",
            "latest_version_href": "/pulp/api/v3/repositories/rpm/rpm/123/versions/1"
        })

        mock_get_remote.return_value = RpmRemote(**{
            "pulp_href": "/pulp/api/v3/remotes/rpm/rpm/123",
            "name": "test-rpm",
            "url": "https://pulp.example.com/",
            "policy": "immediate"
        })

        task = self.task_repository.add(**{
            "name": "test removed banned packages skip2",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })
        self.db.flush()

        result = self.repo_syncher._start_remove_banned_packages(task)
        assert result == False

    @patch("pulp_manager.app.services.repo_syncher.get_repo")
    def test_start_sync_fail(self, mock_get_repo):
        """Tests the exception handling code path for when an issue occurs during the initiation
        of a pulp repo sync
        """

        mock_get_repo.side_effect = Mock(side_effect=Exception('Test'))

        task_crud = self.repo_syncher._task_crud
        # fake dummy task, doesn't need all correct fields
        # filled in for the test
        task = task_crud.get_by_id(1)

        with pytest.raises(Exception):
            task_stage = self.repo_syncher._start_sync(task)

    @patch("pulp_manager.app.services.repo_syncher.get_repo")
    @patch("pulp_manager.app.services.pulp_manager.new_publication")
    @patch("pulp_manager.app.services.pulp_manager.new_pulp_client")
    @patch("pulp_manager.app.services.pulp_manager.PulpManager._get_deb_signing_service")
    def test_start_publication_rpm(self, mock_get_deb_signing_service, mock_new_pulp_client,
            mock_new_publication, mock_get_repo):
        """Tests when there are no issues starting the publication of an RPM repository
        the task starts successfully
        """

        def new_pulp_client(pulp_server: PulpServer):
            return Pulp3Client(pulp_server.name, username=pulp_server.username, password="test")

        mock_new_pulp_client.side_effect = new_pulp_client

        mock_get_deb_signing_service.return_value = ""

        mock_new_publication.return_value = PulpTask(**{
            "pulp_href": "/pulp/api/v3/tasks/123/",
            "pulp_created": datetime.now(),
            "state": "running",
            "name": "new-publication",
            "logging_cid": "1234"
        })
        
        task = self.task_repository.add(**{
            "name": "test start publication rpm",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })
        self.db.flush()

        mock_get_repo.return_value = RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/1",
            "name": "fake-rpm-repo",
            "latest_version_href": "/pulp/api/v3/repositories/rpm/rpm/1/versions/1"
        })

        mock_new_publication.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/789",
            "pulp_created": datetime.utcnow(),
            "state": "running",
            "name": "repo-publication",
            "logging_cid": "123"
        })

        task_stage = self.repo_syncher._start_publication(task)
        call_args, call_kwargs = mock_new_publication.call_args
        assert isinstance(call_args[1], RpmPublication)
        assert "publish repo" in task_stage.name
        assert task_stage.task_id == task.id

    @patch("pulp_manager.app.services.repo_syncher.get_repo")
    @patch("pulp_manager.app.services.pulp_manager.new_publication")
    @patch("pulp_manager.app.services.pulp_manager.new_pulp_client")
    @patch("pulp_manager.app.services.pulp_manager.PulpManager._get_deb_signing_service")
    def test_start_publication_deb(self, mock_get_deb_signing_service, mock_new_pulp_client,
            mock_new_publication, mock_get_repo):
        """Tests when there are no issues starting the publication of an DEB repository
        the task starts successfully
        """

        def new_pulp_client(pulp_server: PulpServer):
            return Pulp3Client(pulp_server.name, username=pulp_server.username, password="test")

        mock_new_pulp_client.side_effect = new_pulp_client
        mock_get_deb_signing_service.return_value = ""

        mock_new_publication.return_value = PulpTask(**{
            "pulp_href": "/pulp/api/v3/tasks/123/",
            "pulp_created": datetime.now(),
            "state": "running",
            "name": "new-publication",
            "logging_cid": "1234"
        })

        task = self.task_repository.add(**{
            "name": "test start publication rpm",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/deb/apt/123"}
        })
        self.db.flush()

        mock_get_repo.return_value = DebRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/deb/apt/1",
            "name": "fake-rpm-repo",
            "latest_version_href": "/pulp/api/v3/repositories/deb/apt/1/versions/1"
        })

        mock_new_publication.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/789",
            "pulp_created": datetime.utcnow(),
            "state": "running",
            "name": "repo-publication",
            "logging_cid": "123"
        })

        task_stage = self.repo_syncher._start_publication(task)
        call_args, call_kwargs = mock_new_publication.call_args
        assert isinstance(call_args[1], DebPublication)
        assert "publish repo" in task_stage.name
        assert task_stage.task_id == task.id

    @patch("pulp_manager.app.services.repo_syncher.get_repo")
    @patch("pulp_manager.app.services.pulp_manager.new_publication")
    def test_start_publication_fail(self, mock_new_publication, mock_get_repo):
        """Tests when there are no issues starting the publication of an RPM repository
        the task starts successfully
        """
        task = self.task_repository.add(**{
            "name": "test start publication fail",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })
        self.db.flush()

        mock_get_repo.return_value = RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/1",
            "name": "fake-rpm-repo",
            "latest_version_href": "/pulp/api/v3/repositories/rpm/rpm/1/versions/1"
        })

        mock_new_publication.side_effect = Mock(side_effect=Exception('Test'))

        with pytest.raises(Exception):
            task_stage = self.repo_syncher._start_publication(task)

    @patch("pulp_manager.app.services.repo_syncher.get_repo")
    @patch("pulp_manager.app.services.repo_syncher.get_all_publications")
    def test_publication_exists_true(self, mock_get_all_publications, mock_get_repo):
        """Tests that what a publication exists for a repos given repository version True
        is returned
        """

        mock_get_repo.return_value = RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/123",
            "name": "test-rpm",
            "latest_version_href": "/pulp/api/v3/repositories/rpm/rpm/123/versions/1"
        })

        mock_get_all_publications.return_value = [RpmPublication(**{
            "pulp_href": "/pulp/api/v3/publications/rpm/rpm/123",
            "repository_version": "/pulp/api/v3/repositories/rpm/rpm/123/versions/1",
            "repository": "/pulp/api/v3/repositories/rpm/rpm/123",
            "metadata_checksum_type": "unknown",
            "package_checksum_type": "unknown"
        })]

        result = self.repo_syncher._publication_exists("pulp/api/v3/repositories/rpm/rpm/123")
        assert result == True

    @patch("pulp_manager.app.services.repo_syncher.get_repo")
    @patch("pulp_manager.app.services.repo_syncher.get_all_publications")
    def test_publication_exists_false(self, mock_get_all_publications, mock_get_repo):
        """Tests that what a publication exists for a repos given repository version False
        is returned
        """

        mock_get_repo.return_value = RpmRepository(**{
            "pulp_href": "/pulp/api/v3/repositories/rpm/rpm/123",
            "name": "test-rpm",
            "latest_version_href": "/pulp/api/v3/repositories/rpm/rpm/123/versions/1"
        })
        mock_get_all_publications.return_value = []

        result = self.repo_syncher._publication_exists("pulp/api/v3/repositories/rpm/rpm/123")
        assert result == False

    @patch("pulp_manager.app.services.repo_syncher.get_task")
    def test_progress_sync_get_task_fail(self, mock_get_task):
        """Tests that when progressing a sync and there is an error retireving a task from the
        pulp server, True is returned. This indicated that the the task has completed, does not
        matter if success of fail, and it should be removed from the list of tasks that are in
        progress
        """

        task = self.task_repository.add(**{
            "name": "test sync get task failed",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "dummy stage",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        
        self.db.flush()

        mock_get_task.side_effect = Mock(side_effect=Exception('Test'))
        result = self.repo_syncher._progress_sync(task, task_stage)

        assert result == True
        assert task.state == "failed"
        assert task.date_finished is not None
        assert task_stage.error["msg"] is not None
        assert task_stage.error["detail"] is not None

    @patch("pulp_manager.app.services.repo_syncher.get_task")
    def test_progess_sync_pulp_task_failed(self, mock_get_task):
        """Tests that when a pulp task has entered a failed/canceled state that no more stages
        are progressed and the task is marked as failed, and True is returned indicating no
        more tasks should be carries out for the task as a whole
        """

        mock_get_task.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "failed",
            "name": "repo-sync",
            "logging_cid": "123"
        })

        task = self.task_repository.add(**{
            "name": "test sync pulp task failed",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "dummy stage",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        
        self.db.flush()

        result = self.repo_syncher._progress_sync(task, task_stage)
        assert result == True
        assert task.state == "failed"
        assert task.date_finished is not None

    @patch("pulp_manager.app.services.repo_syncher.get_task")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._publication_exists")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._start_remove_banned_packages")
    def test_progess_sync_start_remove_banned_packages(self, mock_start_remove_banned_packages,
            mock_publication_exists, mock_get_task):
        """Tests that when a repo sync stage has completed and new resources were created
        on the pulp server, and _start_remove_banned_packages returns True to indicate
        that a stage needed to run to remove packages from the current repo version, then False
        is retruned to show the sync is still in progress
        """

        mock_get_task.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "completed",
            "name": "repo-sync",
            "logging_cid": "123",
            "created_resources": [
                "/pulp/api/v3/repositories/rpm/rpm/789/versions/1"
            ]
        })

        task = self.task_repository.add(**{
            "name": "test sync start remove banned packages",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "sync repo",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        
        self.db.flush()

        mock_publication_exists.return_value = False
        mock_start_remove_banned_packages.return_value = True

        #import pdb; pdb.set_trace()
        result = self.repo_syncher._progress_sync(task, task_stage)
        assert result == False
        assert mock_start_remove_banned_packages.call_count == 1

    @patch("pulp_manager.app.services.repo_syncher.get_task")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._start_publication")
    def test_progess_sync_start_publication_after_modify_repo(self, mock_start_publication,
            mock_get_task):
        """Tests that after the modification of a repo has completed, a publication
        is started
        """

        mock_get_task.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "completed",
            "name": "modify-repo",
            "logging_cid": "123",
            "created_resources": [
                "/pulp/api/v3/repositories/rpm/rpm/789/versions/1"
            ]
        })

        task = self.task_repository.add(**{
            "name": "test sync start publication after modify",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "remove banned packages",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        
        self.db.flush()

        result = self.repo_syncher._progress_sync(task, task_stage)
        assert result == False
        assert mock_start_publication.call_count == 1

    @patch("pulp_manager.app.services.repo_syncher.get_task")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._publication_exists")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._start_remove_banned_packages")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._start_publication")
    def test_progess_sync_start_publication_no_banned_packages(self, mock_start_publication,
            mock_start_remove_banned_packages, mock_publication_exists, mock_get_task):
        """Tests that when a repo sync stage has completed and are no packages to remove, and new
        resources were created on the pulp server (by this we mean a new repo version has been
        created because the contents of the pulp repo changed after the sync). Also expect False
        to be returned inidicating this task still needs to be tracked and should not be considered
        completed
        """

        mock_get_task.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "completed",
            "name": "repo-sync",
            "logging_cid": "123",
            "created_resources": [
                "/pulp/api/v3/repositories/rpm/rpm/789/versions/1"
            ]
        })

        task = self.task_repository.add(**{
            "name": "test sync start publication no banned packages",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "sync repo",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        self.db.flush()

        mock_publication_exists.return_value = False
        mock_start_remove_banned_packages.return_value = False

        result = self.repo_syncher._progress_sync(task, task_stage)
        assert result == False
        assert mock_start_publication.call_count == 1

    @patch("pulp_manager.app.services.repo_syncher.log.error")
    @patch("pulp_manager.app.services.repo_syncher.get_task")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._publication_exists")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._start_publication")
    def test_progess_sync_skip_start_publication(self, mock_start_publication,
            mock_publication_exists, mock_get_task, mock_log_error):
        """Tests that when a repo sync stage has completed and there were no errors, and no new
        resources were created on the pulp server then the publication step is skipped and True is
        returned indicating that the task should no longer be tracked. Patching of log.error
        because it should not be called
        """

        mock_get_task.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "completed",
            "name": "repo-sync",
            "logging_cid": "123",
            "created_resources": []
        })

        task = self.task_repository.add(**{
            "name": "test sync skip start publication",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "sync repo",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        self.db.flush()
        mock_publication_exists.return_value = True

        result = self.repo_syncher._progress_sync(task, task_stage)
        assert result == True
        assert mock_start_publication.call_count == 0
        assert mock_log_error.call_count == 0

    @patch("pulp_manager.app.services.repo_syncher.log.error")
    @patch("pulp_manager.app.services.repo_syncher.get_task")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._start_publication")
    def test_progess_sync_start_publication_fail(self, mock_start_publication, mock_get_task,
            mock_log_error):
        """Tests that if there is an error in the section that tries to start/skip
        a publication an error is logged and True is returned inicating that the task
        should no longer be tracked as the stage failed
        """
        mock_get_task.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "completed",
            "name": "repo-sync",
            "logging_cid": "123",
            "created_resources": [
                "/pulp/api/v3/repositories/rpm/rpm/789/versions/1"
            ]
        })

        task = self.task_repository.add(**{
            "name": "test sync skip start publication fail",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "sync repo",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        self.db.flush()

        mock_start_publication.side_effect = Mock(side_effect=Exception('Test'))
        result = self.repo_syncher._progress_sync(task, task_stage)
        assert result == True
        assert mock_log_error.call_count == 2

    @patch("pulp_manager.app.services.repo_syncher.get_task")
    def test_process_sync_task_completed_successfully(self, mock_get_task):
        """Tests that when a publish repo task has finished without error the repo
        sync task is marked as completed and True is returned indicating that the task
        should no longer be tracked
        """

        mock_get_task.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "completed",
            "name": "repo-publish",
            "logging_cid": "123",
        })

        task = self.task_repository.add(**{
            "name": "test sync task completed successfully",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "publish repo",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        self.db.flush()

        result = self.repo_syncher._progress_sync(task, task_stage)
        assert result == True
        assert task.state == "completed"
        assert task.date_finished is not None

    @patch("pulp_manager.app.services.repo_syncher.get_task")
    def test_process_sync_task_completed_failure(self, mock_get_task):
        """Tests that when a publish repo task has finished unsuccessfully the repo
        sync task is marked as failed and True is returned indicating that the task
        should no longer be tracked
        """

        mock_get_task.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "failed",
            "name": "repo-publish",
            "logging_cid": "123",
        })

        task = self.task_repository.add(**{
            "name": "test sync task completed failure",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "publish repo",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        self.db.flush()

        result = self.repo_syncher._progress_sync(task, task_stage)
        assert result == True
        assert task.state == "failed"
        assert task.date_finished is not None

    @patch("pulp_manager.app.services.repo_syncher.get_task")
    def test_process_sync_task_still_running(self, mock_get_task):
        """Tests that when a sync/publish task is still running on the pulp server
        False is returned indicating that the task still needs to be tracked
        """

        mock_get_task.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "running",
            "name": "repo-publish",
            "logging_cid": "123",
        })

        task = self.task_repository.add(**{
            "name": "test sync task still running",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "publish repo",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        self.db.flush()

        result = self.repo_syncher._progress_sync(task, task_stage)
        assert result == False

    @patch("pulp_manager.app.services.repo_syncher.get_task")
    def test_process_sync_task_waiting(self, mock_get_task):
        """Tests that when a sync/publish task is in waiting state on the pulp server
        False is returned indicating that the task still needs to be tracked
        """

        mock_get_task.return_value = Pulp3Task(**{
            "pulp_href": "/pulp/api/v3/tasks/123",
            "pulp_created": datetime.utcnow(),
            "state": "waiting",
            "name": "repo-publish",
            "logging_cid": "123",
        })

        task = self.task_repository.add(**{
            "name": "test sync task waiting",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "publish repo",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        self.db.flush()

        result = self.repo_syncher._progress_sync(task, task_stage)
        assert result == False

    def test_update_overall_sync_status(self):
        """Tests that update message for overall sync status is successfully added to the
        current stage on the task
        """

        task = self.task_repository.add(**{
            "name": "test sync task",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"}
        })

        task_stage = TaskStage(**{
            "name": "repo sync",
            "detail": {
                "task_href": "/pulp/api/v3/tasks/123"
            },
            "task": task
        })
        self.db.commit()

        self.repo_syncher._update_overall_sync_status(task, 2, 5, 15)

    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._progress_sync")
    @patch("pulp_manager.app.services.repo_syncher.sleep")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._update_overall_sync_status")
    def test_do_sync_repos(self, mock_update_overall_sync_status, mock_sleep, mock_progress_sync):
        """Tests that a list of tasks for repos to sync eventually completes. Test is
        looking at the flow of code and making sure no unexpected exceptions are being raised.
        Integration tests are need to see how this is working properly
        """
        mock_progress_sync.return_value = True
        
        parent_sync_repos_task = self.task_repository.add(**{
            "name": "test sync repos task",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        repo_task_1 = self.task_repository.add(**{
            "name": "repos task 1",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {
                "pulp_server_repo_id": "1",
                "repo_href": "/pulp/api/v3/repositories/rpm/rpm/123"
            },
            "parent_task": parent_sync_repos_task
        })

        repo_task_2 = self.task_repository.add(**{
            "name": "repos task 2",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {
                "pulp_server_repo_id": "2",
                "repo_href": "/pulp/api/v3/repositories/rpm/rpm/456"
            },
            "parent_task": parent_sync_repos_task
        })

        self.db.commit()
        self.repo_syncher._do_sync_repos(parent_sync_repos_task, [repo_task_1, repo_task_2], 1)

    @patch("pulp_manager.app.services.reconciler.new_pulp_client")
    @patch("pulp_manager.app.services.repo_syncher.PulpReconciler.reconcile")
    def test_reconcile_repos(self, mock_reconcile, mock_new_pulp_client):
        """Tests that when there are no issues with the pulp reconciler, the task
        completes successfully
        """
        task = self.task_repository.add(**{
            "name": "test reconcile",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })
        self.db.flush()
        self.repo_syncher._reconcile_repos(task)

    @patch("pulp_manager.app.services.repo_syncher.PulpReconciler.reconcile")
    def test_reconcile_repos_fail(self, mock_reconcile):
        """Tests that when there are errors in the reoncile of rpeos an exception is raised
        """
        task = self.task_repository.add(**{
            "name": "test reconcile",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })
        self.db.flush()
        mock_reconcile.side_effect = Mock(side_effect=Exception('Test'))

        with pytest.raises(Exception):
            self.repo_syncher._reconcile_repos(task)

    def test_calculate_repo_health_green(self):
        """Tests what the past 5 syncs have had three completed runs
        health status of the repo is set as green
        """

        green_pulp_server = self.pulp_server_repository.add(**{
            "name": "green-pulp-server",
            "username": "username",
            "vault_service_account_mount": "service-accounts"
        })

        green_repo = self.repo_repository.add(**{
            "name": "green-repo",
            "repo_type": "rpm"
        })

        green_pulp_server_repo = self.pulp_server_repo_repository.add(**{
            "pulp_server": green_pulp_server,
            "repo": green_repo,
            "repo_href": "/pulp/api/v3/repositories/rpm/rpm/123",
            "remote_href": "/pulp/api/v3/remotes/rpm/rpm/123",
            "distribution_href": "/pulp/api/v3/distributions/rpm/rpm/123",
        })

        parent_task = self.task_repository.add(**{
            "name": "test green repo health",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task1 = self.task_repository.add(**{
            "name": "failed sync task 1 ",
            "task_type": "repo_sync",
            "state": "failed",
            "date_created": (datetime.utcnow() - timedelta(days=4)),
            "date_started": (datetime.utcnow() - timedelta(days=4)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task2 = self.task_repository.add(**{
            "name": "failed sync task 2",
            "task_type": "repo_sync",
            "state": "failed",
            "date_created": (datetime.utcnow() - timedelta(days=3)),
            "date_started": (datetime.utcnow() - timedelta(days=3)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task3 = self.task_repository.add(**{
            "name": "failed sync task 3",
            "task_type": "repo_sync",
            "state": "failed",
            "date_created": (datetime.utcnow() - timedelta(days=2)),
            "date_started": (datetime.utcnow() - timedelta(days=2)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task4 = self.task_repository.add(**{
            "name": "failed sync task 4",
            "task_type": "repo_sync",
            "state": "failed",
            "date_created": (datetime.utcnow() - timedelta(days=1)),
            "date_started": (datetime.utcnow() - timedelta(days=1)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        # if last task was successfull then we are green
        task5 = self.task_repository.add(**{
            "name": "completed sync task 5",
            "task_type": "repo_sync",
            "state": "completed",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })


        # since the task and repo are linked to their own table also need
        # to fudge the date created here, otherwise the query for pulling
        # back the last five tasks, will not got the ordering right in testing
        pulp_server_repo_task_1 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": green_pulp_server_repo,
            "task": task1,
            "date_created": (datetime.utcnow() - timedelta(days=4))
        })

        pulp_server_repo_task_2 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": green_pulp_server_repo,
            "task": task2,
            "date_created": (datetime.utcnow() - timedelta(days=3))
        })

        pulp_server_repo_task_3 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": green_pulp_server_repo,
            "task": task3,
            "date_created": (datetime.utcnow() - timedelta(days=2))
        })

        pulp_server_repo_task_4 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": green_pulp_server_repo,
            "task": task4,
            "date_created": (datetime.utcnow() - timedelta(days=1))
        })

        pulp_server_repo_task_5 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": green_pulp_server_repo,
            "task": task5
        })

        self.db.commit()

        self.repo_syncher._calculate_repo_health(parent_task, [green_pulp_server_repo])
        assert green_pulp_server_repo.repo_sync_health == "green"

    def test_calculate_repo_health_amber(self):
        """Tests what the past 5 syncs have had three failed runs
        health status of the repo is set as amber
        """

        amber_pulp_server = self.pulp_server_repository.add(**{
            "name": "amber-pulp-server",
            "username": "username",
            "vault_service_account_mount": "service-accounts"
        })

        amber_repo = self.repo_repository.add(**{
            "name": "amber-repo",
            "repo_type": "rpm"
        })

        amber_pulp_server_repo = self.pulp_server_repo_repository.add(**{
            "pulp_server": amber_pulp_server,
            "repo": amber_repo,
            "repo_href": "/pulp/api/v3/repositories/rpm/rpm/123",
            "remote_href": "/pulp/api/v3/remotes/rpm/rpm/123",
            "distribution_href": "/pulp/api/v3/distributions/rpm/rpm/123",
        })

        parent_task = self.task_repository.add(**{
            "name": "test amber repo health",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task1 = self.task_repository.add(**{
            "name": "completed sync task 1 ",
            "task_type": "repo_sync",
            "state": "completed",
            "date_created": (datetime.utcnow() - timedelta(days=4)),
            "date_started": (datetime.utcnow() - timedelta(days=4)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task2 = self.task_repository.add(**{
            "name": "failed sync task 2",
            "task_type": "repo_sync",
            "state": "failed",
            "date_created": (datetime.utcnow() - timedelta(days=3)),
            "date_started": (datetime.utcnow() - timedelta(days=3)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task3 = self.task_repository.add(**{
            "name": "completed sync task 3",
            "task_type": "repo_sync",
            "state": "completed",
            "date_created": (datetime.utcnow() - timedelta(days=2)),
            "date_started": (datetime.utcnow() - timedelta(days=2)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task4 = self.task_repository.add(**{
            "name": "failed sync task 4",
            "task_type": "repo_sync",
            "state": "failed",
            "date_created": (datetime.utcnow() - timedelta(days=1)),
            "date_started": (datetime.utcnow() - timedelta(days=1)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        # if last task was successfull then we are green
        task5 = self.task_repository.add(**{
            "name": "failed sync task 5",
            "task_type": "repo_sync",
            "state": "failed",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })


        # since the task and repo are linked to their own table also need
        # to fudge the date created here, otherwise the query for pulling
        # back the last five tasks, will not got the ordering right in testing
        pulp_server_repo_task_1 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": amber_pulp_server_repo,
            "task": task1,
            "date_created": (datetime.utcnow() - timedelta(days=4))
        })

        pulp_server_repo_task_2 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": amber_pulp_server_repo,
            "task": task2,
            "date_created": (datetime.utcnow() - timedelta(days=3))
        })

        pulp_server_repo_task_3 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": amber_pulp_server_repo,
            "task": task3,
            "date_created": (datetime.utcnow() - timedelta(days=2))
        })

        pulp_server_repo_task_4 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": amber_pulp_server_repo,
            "task": task4,
            "date_created": (datetime.utcnow() - timedelta(days=1))
        })

        pulp_server_repo_task_5 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": amber_pulp_server_repo,
            "task": task5
        })

        self.db.commit()

        self.repo_syncher._calculate_repo_health(parent_task, [amber_pulp_server_repo])
        assert amber_pulp_server_repo.repo_sync_health == "amber"

    def test_calculate_repo_health_red(self):
        """Tests what the past 5 syncs have had four or more failed runs
        health status of the repo is set as red
        """

        red_pulp_server = self.pulp_server_repository.add(**{
            "name": "red-pulp-server",
            "username": "username",
            "vault_service_account_mount": "service-accounts"
        })

        red_repo = self.repo_repository.add(**{
            "name": "red-repo",
            "repo_type": "rpm"
        })

        red_pulp_server_repo = self.pulp_server_repo_repository.add(**{
            "pulp_server": red_pulp_server,
            "repo": red_repo,
            "repo_href": "/pulp/api/v3/repositories/rpm/rpm/123",
            "remote_href": "/pulp/api/v3/remotes/rpm/rpm/123",
            "distribution_href": "/pulp/api/v3/distributions/rpm/rpm/123",
        })

        parent_task = self.task_repository.add(**{
            "name": "test red repo health",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task1 = self.task_repository.add(**{
            "name": "failed sync task 1 ",
            "task_type": "repo_sync",
            "state": "failed",
            "date_created": (datetime.utcnow() - timedelta(days=4)),
            "date_started": (datetime.utcnow() - timedelta(days=4)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task2 = self.task_repository.add(**{
            "name": "failed sync task 2",
            "task_type": "repo_sync",
            "state": "failed",
            "date_created": (datetime.utcnow() - timedelta(days=3)),
            "date_started": (datetime.utcnow() - timedelta(days=3)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task3 = self.task_repository.add(**{
            "name": "failed sync task 3",
            "task_type": "repo_sync",
            "state": "failed",
            "date_created": (datetime.utcnow() - timedelta(days=2)),
            "date_started": (datetime.utcnow() - timedelta(days=2)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        task4 = self.task_repository.add(**{
            "name": "failed sync task 4",
            "task_type": "repo_sync",
            "state": "failed",
            "date_created": (datetime.utcnow() - timedelta(days=1)),
            "date_started": (datetime.utcnow() - timedelta(days=1)),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        # if last task was successfull then we are green
        task5 = self.task_repository.add(**{
            "name": "failed sync task 5",
            "task_type": "repo_sync",
            "state": "failed",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })


        # since the task and repo are linked to their own table also need
        # to fudge the date created here, otherwise the query for pulling
        # back the last five tasks, will not got the ordering right in testing
        pulp_server_repo_task_1 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": red_pulp_server_repo,
            "task": task1,
            "date_created": (datetime.utcnow() - timedelta(days=4))
        })

        pulp_server_repo_task_2 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": red_pulp_server_repo,
            "task": task2,
            "date_created": (datetime.utcnow() - timedelta(days=3))
        })

        pulp_server_repo_task_3 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": red_pulp_server_repo,
            "task": task3,
            "date_created": (datetime.utcnow() - timedelta(days=2))
        })

        pulp_server_repo_task_4 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": red_pulp_server_repo,
            "task": task4,
            "date_created": (datetime.utcnow() - timedelta(days=1))
        })

        pulp_server_repo_task_5 = self.pulp_server_repo_task_repository.add(**{
            "pulp_server_repo": red_pulp_server_repo,
            "task": task5
        })

        self.db.commit()

        self.repo_syncher._calculate_repo_health(parent_task, [red_pulp_server_repo])
        assert red_pulp_server_repo.repo_sync_health == "red"

    @patch("pulp_manager.app.services.repo_syncher.PulpServerRepoTaskRepository.filter_paged")
    @patch("pulp_manager.app.services.repo_syncher.log.error")
    def test_calculate_repo_health_fail(self, mock_log_error, patched_filter_paged):
        """Tests that when an unexpected error occurs during repo health calculation
        an exception is raised and a log message written
        """

        parent_task = self.task_repository.add(**{
            "name": "test red repo health",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        self.db.flush()
        pulp_server_repo = self.pulp_server_repo_repository.first()
        

        patched_filter_paged.side_effect = Mock(side_effect=Exception('Test'))
        with pytest.raises(Exception):
            self.repo_syncher._calculate_repo_health(parent_task, [pulp_server_repo])

        assert mock_log_error.call_count == 2

    @patch("pulp_manager.app.services.repo_syncher.new_pulp_client")
    def test_calculate_pulp_server_repo_health_rollup_green(self, mock_new_pulp_client):
        """Tests that when all repos have a repo sync status of green the repo
        sync health roll up for the pulp server is also reported as green
        """

        def new_pulp_client(pulp_server: PulpServer):
            return Pulp3Client(pulp_server.name, username=pulp_server.username, password="test")

        mock_new_pulp_client.side_effect = new_pulp_client

        pulp_server = self.pulp_server_repository.add(**{
            "name": "green-pulp-health-rollup",
            "username": "username",
            "vault_service_account_mount": "service-accounts"
        })

        repo1 = self.repo_repository.add(**{
            "name": "green-pulp-health-rollup-1",
            "repo_type": "rpm"
        })

        repo2 = self.repo_repository.add(**{
            "name": "green-pulp-health-rollup-2",
            "repo_type": "rpm"
        })

        repo3 = self.repo_repository.add(**{
            "name": "green-pulp-health-rollup-3",
            "repo_type": "rpm"
        })

        self.pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": repo1,
            "repo_sync_health": "green",
            "repo_href": "/pilp/api/repositories/rpm/rpm/123"
        })

        self.pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": repo2,
            "repo_sync_health": "green",
            "repo_href": "/pilp/api/repositories/rpm/rpm/456"
        })

        self.pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": repo3,
            "repo_sync_health": "green",
            "repo_href": "/pilp/api/repositories/rpm/rpm/789"
        })

        task = self.task_repository.add(**{
            "name": "test sync with green health rollup",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        self.db.commit()

        repo_syncher = RepoSyncher(self.db, "green-pulp-health-rollup")
        repo_syncher._calculate_pulp_server_repo_health_rollup(task)
        assert pulp_server.repo_sync_health_rollup == "green"

    @patch("pulp_manager.app.services.repo_syncher.new_pulp_client")
    def test_calculate_pulp_server_repo_health_rollup_amber(self, mock_new_pulp_client):
        """Tests that when one repo sync status is in amber and the rest are green
        sync health roll up for the pulp server is also reported as amber
        """

        def new_pulp_client(pulp_server: PulpServer):
            return Pulp3Client(pulp_server.name, username=pulp_server.username, password="test")

        mock_new_pulp_client.side_effect = new_pulp_client

        pulp_server = self.pulp_server_repository.add(**{
            "name": "amber-pulp-health-rollup",
            "username": "username",
            "vault_service_account_mount": "service-accounts"
        })

        repo1 = self.repo_repository.add(**{
            "name": "amber-pulp-health-rollup-1",
            "repo_type": "rpm"
        })

        repo2 = self.repo_repository.add(**{
            "name": "amber-pulp-health-rollup-2",
            "repo_type": "rpm"
        })

        repo3 = self.repo_repository.add(**{
            "name": "amber-pulp-health-rollup-3",
            "repo_type": "rpm"
        })

        # Only setting one to amber as that is enough for health sync rollup
        # to report amber is everything else is good
        self.pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": repo1,
            "repo_sync_health": "green",
            "repo_href": "/pilp/api/repositories/rpm/rpm/123"
        })

        self.pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": repo2,
            "repo_sync_health": "amber",
            "repo_href": "/pilp/api/repositories/rpm/rpm/456"
        })

        self.pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": repo3,
            "repo_sync_health": "green",
            "repo_href": "/pilp/api/repositories/rpm/rpm/789"
        })

        task = self.task_repository.add(**{
            "name": "test sync with amber health rollup",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        self.db.commit()

        repo_syncher = RepoSyncher(self.db, "amber-pulp-health-rollup")
        repo_syncher._calculate_pulp_server_repo_health_rollup(task)
        assert pulp_server.repo_sync_health_rollup == "amber"

    @patch("pulp_manager.app.services.repo_syncher.new_pulp_client")
    def test_calculate_pulp_server_repo_health_rollup_red(self, mock_new_pulp_client):
        """Tests that when one repo sync status is in red and the rest are green/amber
        sync health roll up for the pulp server is also reported as red
        """

        def new_pulp_client(pulp_server: PulpServer):
            return Pulp3Client(pulp_server.name, username=pulp_server.username, password="test")

        mock_new_pulp_client.side_effect = new_pulp_client

        pulp_server = self.pulp_server_repository.add(**{
            "name": "red-pulp-health-rollup",
            "username": "username",
            "vault_service_account_mount": "service-accounts"
        })

        repo1 = self.repo_repository.add(**{
            "name": "red-pulp-health-rollup-1",
            "repo_type": "rpm"
        })

        repo2 = self.repo_repository.add(**{
            "name": "red-pulp-health-rollup-2",
            "repo_type": "rpm"
        })

        repo3 = self.repo_repository.add(**{
            "name": "red-pulp-health-rollup-3",
            "repo_type": "rpm"
        })

        # Only setting one to red as that is enough for health sync rollup
        # to report red despite status of all other repos
        self.pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": repo1,
            "repo_sync_health": "green",
            "repo_href": "/pilp/api/repositories/rpm/rpm/123"
        })

        self.pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": repo2,
            "repo_sync_health": "amber",
            "repo_href": "/pilp/api/repositories/rpm/rpm/456"
        })

        self.pulp_server_repo_repository.add(**{
            "pulp_server": pulp_server,
            "repo": repo3,
            "repo_sync_health": "red",
            "repo_href": "/pilp/api/repositories/rpm/rpm/789"
        })

        task = self.task_repository.add(**{
            "name": "test sync with red health rollup",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })

        self.db.commit()

        repo_syncher = RepoSyncher(self.db, "red-pulp-health-rollup")
        repo_syncher._calculate_pulp_server_repo_health_rollup(task)
        assert pulp_server.repo_sync_health_rollup == "red"

    @patch("pulp_manager.app.repositories.PulpServerRepository.update")
    @patch("pulp_manager.app.services.repo_syncher.log.error")
    def test_calculate_pulp_server_repo_health_rollup_fail(self, mock_log_error, patched_update):
        """Tests that when an exception is raiesd during the calculation of rpeo health an
        exception is logged
        """

        task = self.task_repository.add(**{
            "name": "sync repos",
            "task_type": "repo_sync",
            "state": "running",
            "date_started": datetime.utcnow(),
            "worker_name": socket.getfqdn(),
            "worker_job_id": "abc123",
            "task_args": {"arg1": "val1", "arg2": "val2"}
        })
        self.db.commit()

        patched_update.side_effect = Mock(side_effect=Exception('Test'))

        with pytest.raises(Exception):
            self.repo_syncher._calculate_pulp_server_repo_health_rollup(task)

        mock_log_error.call_count == 2

    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._reconcile_repos")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._get_repos_to_sync")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._generate_tasks")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._do_sync_repos")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._calculate_repo_health")
    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._calculate_pulp_server_repo_health_rollup")
    def test_sync_repos(self, mock_calculate_pulp_server_repo_health_rollup,
            mock_calculate_repo_health, mock_do_sync_repos, mock_generate_tasks,
            mock_get_repos_to_sync, mock_reconcile_repos):
        """This test checks flow of code so that if no errors are generated then not exceptions
        should be raised. Mocks out most of the method calls which have been tested in previous
        sections
        """

        self.repo_syncher.sync_repos(2)

    @patch("pulp_manager.app.services.repo_syncher.RepoSyncher._reconcile_repos")
    @patch("pulp_manager.app.services.repo_syncher.log.error")
    def test_sync_repos_fail(self, mock_log_error, mock_reconcile_repos):
        """This test checks flow of code, tests if exception is raised during sync of repos
        it is caught logged and re raised
        """

        mock_reconcile_repos.side_effect = Mock(side_effect=Exception('Test'))
        with pytest.raises(Exception):
            self.repo_syncher.sync_repos(2)
 
        assert mock_log_error.call_count == 2
