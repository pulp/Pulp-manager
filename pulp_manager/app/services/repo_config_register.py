"""Class that registers repos on the pulp server from defined git config
"""
import json
import os
import re
import shutil
import socket
import tempfile
import traceback
from datetime import datetime

from git import Repo
from rq import get_current_job
from sqlalchemy.orm import Session

from hashi_vault_client.hashi_vault_client.client import HashiVaultClient
from pulp_manager.app.config import CONFIG
from pulp_manager.app.repositories import TaskRepository
from pulp_manager.app.services.base import PulpServerService
from pulp_manager.app.services.pulp_manager import PulpManager
from pulp_manager.app.utils import log


#pylint:disable=unspecified-encoding
class RepoConfigRegister(PulpServerService):
    """Carries out registartion of repos on the target pulp server based on
    repo config geld in git
    """

    def __init__(self, db: Session, name: str):
        """Constructor
        :param db: DB session to use
        :type db: Session
        :param name: name of the pulp server to manage
        :type name: str
        """

        self._db = db
        self._pulp_server_name = name
        self._pulp_manager = PulpManager(db, name)
        self._task_crud = TaskRepository(db)

        job = get_current_job()
        self._job_id = job.id if job else None

    def _clone_pulp_repo_config(self):
        """Creates a temporary directory to clone the repo config defined in CONFIG.
        Returns the path to the directory that was created

        :return: str
        """

        temp_dir = tempfile.mkdtemp(prefix="pulp_manager", dir="/tmp")
        log.info(f"created {temp_dir} to clone repo config into")
        Repo.clone_from(CONFIG["pulp"]["git_repo_config"], temp_dir)
        log.info(f"clone into {temp_dir} completed")
        return os.path.join(temp_dir, CONFIG["pulp"]["git_repo_config_dir"])

    #pylint:disable=line-too-long,too-many-branches
    def _generate_repo_config_from_file(self, file_path: str):
        """From the path of the given repo config file, generates a dict that can be used
        for creating/updating a repo

        :param file_path: path to the file that contains config for the repo
        :type file_path: str
        :return: dict
        """

        global_config = {}
        repo_config = {}
        log.debug(f"generating config for repo at file path {file_path}")

        if file_path.find("/remote/") != -1:
            global_file_path = file_path[0:file_path.find("/remote/")] + "/remote/global.json"

            if os.path.isfile(global_file_path):
                log.debug(f"Loading global config from {global_file_path}")
                with open(global_file_path, 'r') as global_file:
                    global_config = json.loads(global_file.read())

        with open(file_path, "r") as repo_config_file:
            loaded_repo_config = json.loads(repo_config_file.read())
            loaded_repo_config.update(global_config)

            name = loaded_repo_config["name"]
            repo_type = loaded_repo_config["content_repo_type"].replace("iso", "file")
            repo_config["repo_type"] = repo_type
            global_config_package_prefix = ""
            if "pulp" in global_config:
                global_config_package_prefix = global_config["pulp"]["package_prefix"]

            if "url" in loaded_repo_config:
                if not name.startswith("ext-"):
                    name = f"ext-{name}"

                tls_validation = False
                if "tls_validation" in loaded_repo_config:
                    tls_validation = loaded_repo_config["tls_validation"]

                repo_config.update({
                    "name": name,
                    # This will be improved once moved away from Pulp2
                    "url": loaded_repo_config["url"].replace("{{hpe_fwpp_token}}:null@", ""),
                    "proxy_url": loaded_repo_config["proxy"],
                    "tls_validation": tls_validation
                })

                # Remove proxy if synching a remote through an internal GR server.
                # E.g. synching jenkins repos through artifactory because
                # of redirects being blocked by proxy when synching RPMs
                for domain_name in CONFIG["pulp"]["internal_domains"].split(","):
                    if domain_name in repo_config["url"]:
                        repo_config["proxy_url"] = None
                        break

                if repo_type == "deb":
                    if "releases" in loaded_repo_config:
                        repo_config["distributions"] = loaded_repo_config["releases"]
                    else:
                        repo_config["distributions"] = "stable"

                    if "architectures" in loaded_repo_config:
                        repo_config["architectures"] = loaded_repo_config["architectures"]

                    if "components" in loaded_repo_config:
                        repo_config["components"] = loaded_repo_config["components"]
                        repo_config["ignore_missing_package_indices"] = True

                if "vault_load_secrets" in loaded_repo_config:
                    log.info(f"Loading secrets for {name} from hashicorp vault")
                    vault_namespace = CONFIG["vault"]["repo_secret_namespace"]
                    hashi_client = HashiVaultClient(
                        url=CONFIG["vault"]["vault_addr"],
                        vault_agent=True,
                        namespace=vault_namespace
                    )
                    for secret_to_load in loaded_repo_config["vault_load_secrets"]:
                        log.debug(
                            f"Loading secret {secret_to_load['secret_name']} from namespace "
                            f"{vault_namespace}, in kv {secret_to_load['kv']} at "
                            f"{secret_to_load['path']} for {secret_to_load['remote_property']}"
                        )

                        result = hashi_client.read_kv_secret(
                            secret_to_load["path"], secret_to_load["kv"]
                        )
                        repo_config[secret_to_load["remote_property"]] = result[secret_to_load["secret_name"]]

            elif not name.startswith(global_config_package_prefix):
                name = f"{global_config_package_prefix}{name}"

        repo_config.update({
            "name": name,
            "description": (f"{loaded_repo_config['description']} - {loaded_repo_config['owner']} "
                            f"- base_url:{loaded_repo_config['base_url']}")
        })

        return repo_config

    def _apply_repo_name_prefix(self, name: str, root_path: str) -> str:
        """Applies the appropriate prefix to a repo name based on whether it's remote or internal.

        :param name: The repo name
        :type name: str
        :param root_path: The root directory path containing the repo config file
        :type root_path: str
        :return: The repo name with appropriate prefix applied
        :rtype: str
        """
        if "remote" in root_path and not name.startswith("ext-"):
            return f"ext-{name}"
        elif "internal" in root_path:
            prefix = CONFIG["pulp"]["internal_package_prefix"]
            if not name.startswith(prefix):
                return f"{prefix}{name}"
        return name

    def _parse_repo_config_files(self, repo_config_dir: str, regex_include: str,
            regex_exclude: str):
        """Parses the repo configs in the given repo_config_dir and returns a list of dicts
        which contains all the options for creating repos on the Pulp Server

        :param repo_config_dir: Path to the directory that contains the checked out
                                pulp repo config
        :type repo_config_dir: str
        :return: List
        """

        #pylint:disable=unused-variable
        parsed_repo_configs = []
        for root, directories, files in os.walk(repo_config_dir):
            for found_file in files:
                if found_file.endswith(".json") and "global.json" not in found_file:
                    name = None
                    file_path = os.path.join(root, found_file)

                    with open(file_path, "r") as repo_config_file:
                        repo_config = json.loads(repo_config_file.read())
                        name = repo_config["name"]

                    name = self._apply_repo_name_prefix(name, root)

                    if regex_exclude and re.search(regex_exclude, name):
                        continue
                    if regex_include and not re.search(regex_include, name):
                        continue

                    parsed_repo_configs.append(self._generate_repo_config_from_file(file_path))

        return parsed_repo_configs

    def create_repos_from_git_config(self, regex_include: str=None, regex_exclude: str=None):
        """Creates/updates repos on the target pulp server with repo config that is defined in git.
        The repo to clone from is defined in the confi.ini
        """

        current_repo = None
        task = self._task_crud.add(**{
            "name": f"{self._pulp_server_name} repo registartion",
            "date_started": datetime.utcnow(),
            "task_type": "repo_creation_from_git",
            "state": "running",
            "worker_name": socket.gethostname(),
            "worker_job_id": self._job_id,
            "task_args": {
                "regex_include": regex_include,
                "regex_exclude": regex_exclude
            }
        })
        self._db.commit()

        repo_config_dir = None

        try:
            repo_config_dir = self._clone_pulp_repo_config()
            log.debug("repo cloned to {repo_config_dir}")
            repo_configs = self._parse_repo_config_files(
                repo_config_dir, regex_include, regex_exclude
            )

            for config in repo_configs:
                log.debug(f"create/update repo for {config['name']}")
                current_repo = config
                self._pulp_manager.create_or_update_repository(**config)

            self._task_crud.update(task, **{
                "state": "completed", "date_finished": datetime.utcnow()
            })
            self._db.commit()
        except Exception:
            message = f"unexpected error occured registering repos on {self._pulp_server_name}"
            if current_repo:
                message = f"failed to create/update repo for {config['name']}"
            log.error(message)
            log.error(traceback.format_exc())

            # pylint:disable=duplicate-code
            self._task_crud.update(task, **{
                "state": "failed",
                "date_finished": datetime.utcnow(),
                "error": {
                    "msg": message,
                    "detail": traceback.format_exc()
                }
            })

            self._db.commit()
            raise
        finally:
            if repo_config_dir:
                log.debug(f"tidying up cloned repo {repo_config_dir}")
                shutil.rmtree(repo_config_dir)
