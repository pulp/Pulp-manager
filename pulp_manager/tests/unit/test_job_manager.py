
"""Tests for ensuring jobs are correct added to redis
"""

import pytest
import fakeredis
from mock import patch, MagicMock, Mock
from rq import Queue
from rq.job import Job
from rq_scheduler import Scheduler

from pulp_manager.app.database import session, engine
from pulp_manager.app.exceptions import (
    PulpManagerEntityNotFoundError, PulpManagerTaskInvalidStateError
)
from pulp_manager.app.repositories import (
    PulpServerRepository, PulpServerRepoGroupRepository, TaskRepository
)
from pulp_manager.app.models import Task
from pulp_manager.app.job_manager import JobManager


class TestJobManager:
    """Tests for job manager functions
    """

    @patch("pulp_manager.app.job_manager.Redis")
    def setup_method(self, method, mock_redis):
        """Fake out the db, config and replace redis with fakeredis
        """

        self.db = session()
        mock_redis.return_value = fakeredis.FakeStrictRedis()
        self.job_manager = JobManager(self.db)
        self.pulp_server_repository = PulpServerRepository(self.db)
        self.pulp_server_repo_group_repository = PulpServerRepoGroupRepository(self.db)
        self.task_repository = TaskRepository(self.db)

    def teardown_method(self):
        """Ensure db connections are closed
        """

        self.db.close()
        engine.dispose()

    def test_setup_pulp_server_repo_group_scheduled_jobs(self):
        """Tests that the correct jobs get added to the schedulers queue
        """

        pulp_server = self.pulp_server_repository.add(**{
            "name": "test_pulp_repo_group_schedule",
            "username": "user1",
            "vault_service_account_mount": "service-accounts",
            "repo_sync_health_rollup": "green"
        })

        pulp_server_repo_group1 = self.pulp_server_repo_group_repository.add(**{
            "pulp_server": pulp_server,
            "repo_group_id": 1,
            "max_concurrent_syncs": 2,
            "schedule": "0 0 * * *",
            "max_runtime": "24h"
        })

        self.db.flush()
        pulp_server = self.pulp_server_repository.get_pulp_server_with_repo_groups(**{
            "id": pulp_server.id
        })[0]

        self.job_manager._setup_pulp_server_repo_group_scheduled_jobs(pulp_server)
        fake_redis = self.job_manager._redis

        default_queue = Queue("default", connection=fake_redis)
        scheduler = Scheduler(queue=default_queue, connection=default_queue.connection)
        # This is a gnereator so we need to enumerate it to carry out more checks
        scheduler_jobs = scheduler.get_jobs()
        jobs = [job for job in scheduler_jobs]

        # Only expect 1 entry as we only schedule repo groups that have a schedule set
        assert len(jobs) == 1

        job = jobs[0]
        # pulp_server_name, max_concurrent_syncs, regex_include, regex_exclude, pulp primary to sync 
        # the regex_include of test-repo is coming from the sample data
        assert job.args == ["test_pulp_repo_group_schedule", 2, "test-repo", None, None]
        assert job.meta["job_type"] == "REPO_GROUP_SYNC_SCHEDULED"
        assert job.meta["pulp_server"] == "test_pulp_repo_group_schedule"
        assert job.meta["repo_group_id"] == pulp_server_repo_group1.repo_group_id
        assert job.meta["max_concurrent_syncs"] == 2
        assert job.meta["regex_include"] == "test-repo"
        assert job.meta["regex_exclude"] == None

        self.db.rollback()

    def test_setup_repo_registration_scheduled_job(self):
        """Tests that a pulp server which is setup for automatic repo registration
        has the job scheduled correctly in redis
        """

        pulp_server = self.pulp_server_repository.add(**{
            "name": "test_pulp_server_repo_registration",
            "username": "user1",
            "vault_service_account_mount": "service-accounts",
            "repo_sync_health_rollup": "green",
            "repo_config_registration_schedule": "0 0 * * *",
            "repo_config_registration_max_runtime": "6h",
            "repo_config_registration_regex_include": None,
            "repo_config_registration_regex_exclude": None
        })

        self.db.flush()

        self.job_manager._setup_repo_registration_scheduled_job(pulp_server)
        fake_redis = self.job_manager._redis

        default_queue = Queue("default", connection=fake_redis)
        scheduler = Scheduler(queue=default_queue, connection=default_queue.connection)
        # This is a gnereator so we need to enumerate it to carry out more checks
        scheduler_jobs = scheduler.get_jobs()
        jobs = [job for job in scheduler_jobs]

        # Only expect 1 entry as we only schedule repo groups that have a schedule set
        assert len(jobs) == 1

        job = jobs[0]
        assert job.args == ["test_pulp_server_repo_registration", None, None, None]
        assert job.meta["job_type"] == "REPO_REGISTRATION_SCHEDULED"
        assert job.meta["pulp_server"] == "test_pulp_server_repo_registration"
        assert job.meta["regex_include"] == None
        assert job.meta["regex_exclude"] == None

        self.db.rollback()

    def test_setup_schedules(self):
        """Tests that the correct jobs get added to the schedulers queue. These
        are generated from the sample data
        """
        self.job_manager.setup_schedules()
        fake_redis = self.job_manager._redis

        default_queue = Queue("default", connection=fake_redis)
        scheduler = Scheduler(queue=default_queue, connection=default_queue.connection)
        # This is a gnereator so we need to enumerate it to carry out more checks
        scheduler_jobs = scheduler.get_jobs()
        jobs = [job for job in scheduler_jobs]

        # Only expect 4 entries as in the sample data, pulp server 1 and 2 have 2 repo groups
        # setup with schedules
        assert len(jobs) == 4

        self.db.rollback()

    def test_queue_remove_content_task(self):
        """Tests that when a snapshot task is queued a Task model is returned
        """

        result = self.job_manager.queue_remove_content_task(
            "test-pulp-server", "test-repo",
            "/pulp/api/v3/packages/rpm/content/1234", "10m"
        )

        assert isinstance(result, Task)
        assert result.task_args["pulp_server_name"] == "test-pulp-server"
        assert result.task_args["repo_name"] == "test-repo"
        assert result.task_args["content_href"] == "/pulp/api/v3/packages/rpm/content/1234"
        assert result.task_args["max_runtime"] == "10m"

    def test_queue_snapshot_task(self):
        """Tests that when a snapshot task is queued a Task model is returned
        """

        result = self.job_manager.queue_snapshot_task(
            "test-pulp-server", "2h", "test-snap", True, "^ext-"
        )

        assert isinstance(result, Task)
        assert result.task_args["max_runtime"] == "2h"
        assert result.task_args["snapshot_prefix"] == "test-snap"
        assert result.task_args["allow_snapshot_reuse"] == True
        assert result.task_args["regex_include"] == "^ext-"
        assert result.task_args["regex_exclude"] == None

    def test_queue_sync_repo_task(self):
        """Tests that when a repo sync task is queued a task is returned
        """

        result = self.job_manager.queue_sync_repo_task(
            "test-pulp-server", "4h", 2
        )

        assert isinstance(result, Task)
        assert result.task_args["max_runtime"] == "4h"
        assert result.task_args["regex_include"] is None
        assert result.task_args["regex_exclude"] is None
        assert result.task_args["max_concurrent_syncs"] == 2
        assert result.task_args["source_pulp_server_name"] is None

    def test_change_task_state_ok(self):
        """Tests that when a task is canclled and is in a valid state to be cancelled the db is
        updated and any associated running rq job is cancelled too
        """

        task = self.task_repository.add(**{
            "name": "test_task",
            "task_type": "repo_snapshot",
            "state": "running",
            "worker_name": "my_worker",
            "worker_job_id": "abc123",
            "task_args_str": ""
        })
        self.db.flush()

        self.job_manager._default_queue.enqueue(print, "test", job_id="abc123")

        task = self.job_manager.change_task_state(task.id, "canceled")
        job = Job.fetch("abc123", connection=self.job_manager._redis)

        assert task.state == "canceled"
        assert job.get_status() == "canceled"

        self.db.rollback()

    def test_change_task_state_fail1(self):
        """Tests that when a task doesn't exist PulpManagerEntityNotFoundError is thrown
        """

        with pytest.raises(PulpManagerEntityNotFoundError):
            task = self.job_manager.change_task_state(99999, "canceled")

    def test_change_task_state_fail2(self):
        """Tests that when a state other than cancelled is passed PulpManagerTaskInvalidStateError
        is raised
        """

        task = self.task_repository.add(**{
            "name": "test_task",
            "task_type": "repo_snapshot",
            "state": "running",
            "worker_name": "my_worker",
            "worker_job_id": "abc123",
            "task_args_str": ""
        })
        self.db.flush()

        with pytest.raises(PulpManagerTaskInvalidStateError):
            self.job_manager.change_task_state(task.id, "kill")

        self.db.rollback()

    def test_change_task_state_fail3(self):
        """Tests that when a task isn't in valid state to be canceled
        PulpManagerTaskInvalidStateError is raised
        """

        task = self.task_repository.add(**{
            "name": "test_task",
            "task_type": "repo_snapshot",
            "state": "running",
            "worker_name": "my_worker",
            "worker_job_id": "abc123",
            "task_args_str": ""
        })
        self.db.flush()

        with pytest.raises(PulpManagerTaskInvalidStateError):
            self.job_manager.change_task_state(task.id, "kill")

        self.db.rollback()
