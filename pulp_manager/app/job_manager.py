"""Service that manages setting up the job schedules and removing schedules that no longer
need to run
"""

import traceback
from datetime import datetime
from typing import Dict

from sqlalchemy.orm import Session
from redis import Redis
from rq import Queue
from rq.command import send_stop_job_command
from rq.job import Job
from rq_scheduler import Scheduler

from pulp_manager.app.database import session
from pulp_manager.app.exceptions import (
    PulpManagerEntityNotFoundError,
    PulpManagerTaskInvalidStateError,
)
from pulp_manager.app.config import CONFIG
from pulp_manager.app.models import PulpServer
from pulp_manager.app.repositories import PulpServerRepository, TaskRepository
from pulp_manager.app.tasks.sync_task import sync_repos
from pulp_manager.app.tasks.remove_content_task import remove_repo_content
from pulp_manager.app.tasks.repo_registration_task import register_repos
from pulp_manager.app.tasks.snapshot_task import snapshot_repos
from pulp_manager.app.tasks.repo_removal_task import remove_repos
from pulp_manager.app.utils import log


REPO_GROUP_SYNC_META = "REPO_GROUP_SYNC_SCHEDULED"
REPO_REGISTRATION_META = "REPO_REGISTRATION_SCHEDULED"


# pylint:disable=redefined-builtin,unused-argument
def fail_task_callback(job, connection, type, value, traceback_exc):
    """When a job in RQ has failed, updates the main task (child tasks aren't touched)
    to be failed along with the exception of what happened
    Args as defined in: https://python-rq.org/docs/
    """

    db = session()

    try:
        task_crud = TaskRepository(db)
        task = task_crud.first(**{"task_id": job.meta["task_id"]})
        if task is not None:
            task_crud.update(
                task,
                **{
                    "state": "failed",
                    "date_finished": datetime.utcnow(),
                    "error": {
                        "msg": "task ran into unexpected error",
                        "detail": traceback.format_list(
                            traceback.extract_tb(traceback_exc)
                        ),
                    },
                },
            )
            db.commit()
    except Exception:
        log.error("fail_task_callback failed")
        log.error(traceback.format_exc())
    finally:
        db.close()


class JobManager:
    """Sets up the scheduled RQ jobs. Needs improving to support sentinel"""

    def __init__(self, db: Session):
        """Constructor
        :param db: db session to use
        :type db: session
        """

        self._db = db
        self._pulp_server_crud = PulpServerRepository(self._db)
        self._task_crud = TaskRepository(self._db)
        self._redis = Redis(
            host=CONFIG["redis"]["host"],
            port=int(CONFIG["redis"]["port"]),
            db=int(CONFIG["redis"]["db"]),
        )
        self._default_queue = Queue("default", connection=self._redis)

    def _setup_pulp_server_repo_group_scheduled_jobs(self, pulp_server: PulpServer):
        """Sets up the defined repo group scheduled jobs for the pulp server.
        Removes any jobs that are no longer in line with the defined config

        :param pulp_server: PulpServer entity to use for setting up scheduled jobs
        :type pulp_server: PulpServer
        """

        log.info(f"setting up scheduled repo groups for {pulp_server.name}")
        scheduler = Scheduler(
            queue=self._default_queue, connection=self._default_queue.connection
        )

        scheduler_jobs = scheduler.get_jobs()

        # Cancel all defined scheduled jobs and then just recreted them
        # as pulp servers shouldn't have a large complicate number of
        # repo group jobs
        for job in scheduler_jobs:
            job_meta = job.meta
            if (
                "job_type" in job_meta
                and job_meta["job_type"] == REPO_GROUP_SYNC_META
                and job_meta["pulp_server"] == pulp_server.name
            ):
                scheduler.cancel(job)

        for repo_group in pulp_server.repo_groups:
            if repo_group.schedule is not None:
                log.info(
                    f"setting up scheduled repo group {repo_group.name} to run "
                    f"at {repo_group.schedule} for {pulp_server.name}"
                )

                pulp_master = None
                if repo_group.pulp_master_id is not None:
                    pulp_master = self._pulp_server_crud.get_by_id(
                        repo_group.pulp_master_id
                    )

                scheduler.cron(
                    repo_group.schedule,
                    func=sync_repos,
                    args=[
                        pulp_server.name,
                        repo_group.max_concurrent_syncs,
                        repo_group.repo_group.regex_include,
                        repo_group.repo_group.regex_exclude,
                        None if pulp_master is None else pulp_master.name,
                    ],
                    result_ttl=172800,
                    timeout=repo_group.max_runtime,
                    queue_name="default",
                    meta={
                        "job_type": REPO_GROUP_SYNC_META,
                        "pulp_server": pulp_server.name,
                        "repo_group_id": repo_group.repo_group_id,
                        "repo_group_name": repo_group.repo_group.name,
                        "max_concurrent_syncs": repo_group.max_concurrent_syncs,
                        "regex_include": repo_group.repo_group.regex_include,
                        "regex_exclude": repo_group.repo_group.regex_exclude,
                        "source_pulp_server_name": (
                            None if pulp_master is None else pulp_master.name
                        ),
                    },
                    on_failure=fail_task_callback,
                )

    def _setup_repo_registration_scheduled_job(self, pulp_server: PulpServer):
        """Sets up jobs for registering repos on the specified pulp server"""

        log.info(f"setting up scheduled repo registration for {pulp_server.name}")
        scheduler = Scheduler(
            queue=self._default_queue, connection=self._default_queue.connection
        )

        scheduler_jobs = scheduler.get_jobs()

        # Cancel all defined scheduled jobs and then just recreted them
        # as pulp servers shouldn't have a large complicate number of
        # repo group jobs
        for job in scheduler_jobs:
            job_meta = job.meta
            if (
                "job_type" in job_meta
                and job_meta["job_type"] == REPO_REGISTRATION_META
                and job_meta["pulp_server"] == pulp_server.name
            ):
                scheduler.cancel(job)

        # Get local config dir from CONFIG if set, otherwise None (will clone from git)
        local_config_dir = CONFIG["pulp"].get("local_repo_config_dir", None)

        scheduler.cron(
            pulp_server.repo_config_registration_schedule,
            func=register_repos,
            on_failure=fail_task_callback,
            args=[
                pulp_server.name,
                pulp_server.repo_config_registration_regex_include,
                pulp_server.repo_config_registration_regex_exclude,
                local_config_dir,
            ],
            result_ttl=172800,
            timeout=pulp_server.repo_config_registration_max_runtime,
            queue_name="default",
            meta={
                "job_type": REPO_REGISTRATION_META,
                "pulp_server": pulp_server.name,
                "regex_include": pulp_server.repo_config_registration_regex_include,
                "regex_exclude": pulp_server.repo_config_registration_regex_exclude,
                "local_repo_config_dir": local_config_dir,
            },
        )

    def setup_schedules(self):
        """Sets up defined scheduled jobs."""

        pulp_servers = self._pulp_server_crud.get_pulp_server_with_repo_groups()
        for pulp_server in pulp_servers:
            self._setup_pulp_server_repo_group_scheduled_jobs(pulp_server)

            if pulp_server.repo_config_registration_schedule:
                self._setup_repo_registration_scheduled_job(pulp_server)

    def queue_sync_repo_task(self, pulp_server: str, max_runtime: str, max_concurrent_syncs: int,
            regex_include: str=None, regex_exclude: str=None, source_pulp_server_name: str=None,
            sync_options: Dict=None):
        """Queue a sync task for the specified pulp server

        :param pulp_server: name of hte pulp server to do the sync for
        :type pulp_server: str
        :param max_runtime: maximum amount of time the sync should run for before it is
                            interrupted. Needs to be in a format rq accepts. -1 means infinite
                            time out
        :type max_runtime: str
        :param max_concurrent_syncs: Maximum number os repos that should be synced at once
        :type max_concurrent_syncs: int
        :param max_concurrent_syncs: Number of repos to sync at once
        :type max_concurrent_syncs: int
        :param regex_include: regex of repos to be included in the repo sync
        :type regex_include: str
        :param regex_exclude: regex of repos to exlude from the repo sync. If there are repos
                              that match both regex_exclude and regex_include, then regex_exclude
                              takes precendence and the repo is excluded from the sync
        :type regex_exclude: str
        :param source_pulp_server_name: the name of the pulp server repos are to be synched from.
                                        This is only needed when synching pulp slaves which don't
                                        sync repos from the internet
        :type source_pulp_server_name: str
        :param sync_options: Additional sync options to be set. These are repo type specific and
                             need to be looked up via the pulp API to see what is valid the group
                             of repos being synced
        :type sync_options: dict
        :return: Task
        """

        task = self._task_crud.add(**{
            "name": f"repo sync {pulp_server}",
            "task_type": "repo_group_sync",
            "task_args": {
                "name": pulp_server,
                "regex_include": regex_include,
                "regex_exclude": regex_exclude,
                "max_runtime": max_runtime,
                "max_concurrent_syncs": max_concurrent_syncs,
                "source_pulp_server_name": source_pulp_server_name,
                "sync_options": sync_options
            },
            "state": "queued"
        })
        self._db.commit()

        try:
            self._default_queue.enqueue(
                sync_repos,
                result_ttl=172800,
                job_timeout=max_runtime,
                kwargs={
                    "pulp_server": pulp_server,
                    "max_concurrent_syncs": max_concurrent_syncs,
                    "regex_include": regex_include,
                    "regex_exclude": regex_exclude,
                    "source_pulp_server_name": source_pulp_server_name,
                    "sync_options": sync_options,
                    "task_id": task.id
                },
                meta={
                    "job_type": "ADHOC_REPO_SYNC",
                    "pulp_server": pulp_server,
                    "max_concurrent_syncs": max_concurrent_syncs,
                    "regex_include": regex_include,
                    "regex_exclude": regex_exclude,
                    "source_pulp_server_name": source_pulp_server_name,
                    "sync_options": sync_options,
                    "task_id": task.id
                },
                on_failure=fail_task_callback,
            )
        except Exception:
            log.error(f"error occured enqueing sync job for {pulp_server}")
            log.error(traceback.format_exc())
            self._task_crud.update(
                task,
                **{
                    "state": "failed",
                    "date_finished": datetime.utcnow(),
                    "error": {
                        "msg": f"error occured enqueing sync repo job for {pulp_server}",
                        "detail": traceback.format_exc(),
                    },
                },
            )

        self._db.commit()
        return task

    def queue_remove_content_task(
        self,
        pulp_server_name: str,
        repo_name: str,
        content_href: str,
        max_runtime: str,
        force_publish: bool = False,
    ):
        """Queues a task that remove the requested content unit from a repo

        :param pulp_server_name: name of the pulp server to remove the content unit from
        :type pulp_server_name: str
        :param repo_name: name of the repo to remove the content unit from
        :type repo_name: str
        :param content_href: pulp href of the content unit to remove
        type content_href: str
        :param max_runtime: Maximuim amount of time job should be allowed to run before
                            it is canclled. Format needs to be given in a way that is valid for RQ
        :type max_runtime: str
        :param force_publish: Force publishing the latest version of the repo even if no content
                              was removed
        :type force_publish: bool
        :returns: Task
        """

        task = self._task_crud.add(
            **{
                "name": f"remove repo content {pulp_server_name}",
                "task_type": "remove_repo_content",
                "task_args": {
                    "max_runtime": max_runtime,
                    "pulp_server_name": pulp_server_name,
                    "repo_name": repo_name,
                    "content_href": content_href,
                    "force_publish": force_publish,
                },
                "state": "queued",
            }
        )
        self._db.commit()

        try:
            self._default_queue.enqueue(
                remove_repo_content,
                result_ttl=172800,
                job_timeout=max_runtime,
                kwargs={
                    "pulp_server_name": pulp_server_name,
                    "repo_name": repo_name,
                    "content_href": content_href,
                    "task_id": task.id,
                    "force_publish": force_publish,
                },
                on_failure=fail_task_callback,
            )
        except Exception:
            log.error(
                f"error occured enqueing remove repo content job for {pulp_server_name}"
            )
            log.error(traceback.format_exc())
            self._task_crud.update(
                task,
                **{
                    "state": "failed",
                    "date_finished": datetime.utcnow(),
                    "error": {
                        "msg": (f"error occurred enqueuing remove repo content job "
                                f"for {pulp_server_name}"),
                        "detail": traceback.format_exc(),
                    },
                },
            )

        self._db.commit()
        return task

    def queue_snapshot_task(
        self,
        pulp_server: str,
        max_runtime: str,
        snapshot_prefix: str,
        allow_snapshot_reuse: bool,
        regex_include: str = None,
        regex_exclude: str = None,
    ):
        """Queues a snapshot task to be picked up a by an RQ worker

                :param pulp_server: name of the pulp server to sync jobs for
            :type pulp_server: str
        :param max_runtime: Maximuim amount of time job should be allowed to run before
                            it is canclled. Format needs to be given in a way that is valid for RQ
        :type max_runtime: str
        :param snapshot_prefix: prefix to put infront of snapshotted repos
        :type snapshot_prefix: str
        :param regex_include: regex of repos to include in the snapshot
            :type regex_include: str
        :param regex_exclude: regex of repos to exlude from the sanpshot. 
        If there are repos that match both regex_exclude and regex_include, then regex_exclude
        takes precendence and the repo is excluded from the snapshot
        :type regex_exclude: str
        """

        task = self._task_crud.add(
            **{
                "name": f"snapshot repos {pulp_server}",
                "task_type": "repo_snapshot",
                "task_args": {
                    "max_runtime": max_runtime,
                    "snapshot_prefix": snapshot_prefix,
                    "regex_include": regex_include,
                    "regex_exclude": regex_exclude,
                    "allow_snapshot_reuse": allow_snapshot_reuse,
                },
                "state": "queued",
            }
        )
        self._db.commit()

        try:
            self._default_queue.enqueue(
                snapshot_repos,
                result_ttl=172800,
                job_timeout=max_runtime,
                kwargs={
                    "pulp_server": pulp_server,
                    "task_id": task.id,
                    "snapshot_prefix": snapshot_prefix,
                    "allow_snapshot_reuse": allow_snapshot_reuse,
                    "regex_include": regex_include,
                    "regex_exclude": regex_exclude,
                },
                on_failure=fail_task_callback,
            )
        except Exception:
            log.error(f"error occured enqueing snapshot job for {pulp_server}")
            log.error(traceback.format_exc())
            self._task_crud.update(
                task,
                **{
                    "state": "failed",
                    "date_finished": datetime.utcnow(),
                    "error": {
                        "msg": f"error occured enqueing snapshot job for {pulp_server}",
                        "detail": traceback.format_exc(),
                    },
                },
            )

        self._db.commit()
        return task

    def change_task_state(self, task_id: int, state: str):
        """Changes the state of the task in the DB to the requested type and if
        there is an RQ task running cancel is called on it. Currently only supports
        changing a task to be in the canceled state

        :param task_id: ID of the task to change the state of
        :type task_id: int
        :param state: state to change the task to
        :type state: str
        :return: task
        """

        task = self._task_crud.get_by_id(task_id)
        if task is None:
            raise PulpManagerEntityNotFoundError(f"task with id {task_id} not found")

        if state != "canceled":
            raise PulpManagerTaskInvalidStateError(
                "canceled is the only valid state to move a task to"
            )

        if task.state not in ["queued", "running"]:
            raise PulpManagerTaskInvalidStateError(
                f"task cannot be moved to {state} when it is in {task.state}"
            )

        if task.worker_job_id is not None:
            job = Job.fetch(task.worker_job_id, connection=self._redis)
            if job.get_status() in ["queued", "deferred", "scheduled"]:
                job.cancel()
            elif job.get_status() == "started":
                send_stop_job_command(self._redis, task.worker_job_id)

        self._task_crud.update(
            task, **{"state": "canceled", "date_finished": datetime.utcnow()}
        )
        self._db.commit()

        return task

    def queue_removal_task(
        self,
        pulp_server: str,
        max_runtime: str,
        regex_include: str = None,
        regex_exclude: str = None,
        dry_run: bool = True,
    ):
        """Queues a repository removal task to be picked up by an RQ worker.

        :param pulp_server: Name of the Pulp server for which repos will be removed.
        :type pulp_server: str
        :param max_runtime: Maximum amount of time the job should be allowed to run
                            before it is cancelled. Format needs to be valid for RQ.
        :type max_runtime: str
        :param regex_include: Regex pattern of repos to include in the removal.
        :type regex_include: str, optional
        :param regex_exclude: Regex pattern of repos to exclude from the removal. Repos
                            matching both include and exclude patterns will be excluded.
        :type regex_exclude: str, optional
        :param dry_run: If true, performs a trial run without actually deleting any repos.
        :type dry_run: bool
        """

        task = self._task_crud.add(
            **{
                "name": f"remove repos {pulp_server}",
                "task_type": "repo_removal",
                "task_args": {
                    "max_runtime": max_runtime,
                    "regex_include": regex_include,
                    "regex_exclude": regex_exclude,
                    "dry_run": dry_run,
                },
                "state": "queued",
            }
        )
        self._db.commit()

        try:
            self._default_queue.enqueue(
                remove_repos,
                result_ttl=172800,
                job_timeout=max_runtime,
                kwargs={
                    "pulp_server": pulp_server,
                    "task_id": task.id,
                    "regex_include": regex_include,
                    "regex_exclude": regex_exclude,
                    "dry_run": dry_run,
                },
                on_failure=fail_task_callback,
            )
        except Exception as e:
            log.error(f"Error occurred enqueueing removal job for {pulp_server}: {e}")
            log.error(traceback.format_exc())
            self._task_crud.update(
                task, **{"state": "failed", "date_finished": datetime.utcnow()}
            )

        return task
