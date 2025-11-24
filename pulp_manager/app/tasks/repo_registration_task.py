"""Scheduled job for registering repos on a Pulp Server
"""

import traceback
from pulp_manager.app.database import session
from pulp_manager.app.services import RepoConfigRegister
from pulp_manager.app.utils import log


def register_repos(pulp_server: str, regex_include: str=None, regex_exclude: str=None,
                   local_repo_config_dir: str=None):
    """Task that is used to register repos on a pulp server

    :param pulp_server: name of the pulp server to register the repos for
    :type pulp_server: str
    :param regex_include: Regex of repo names to include from repo config
    :type regex_include: str
    :param regex_exclude: Regex of repo names to exclude from repo config. If there is a match
                          with regex_exclude and regex_include. regex_exclude takes precendence
                          and the repo will not be added to the pulp server
    :type regex_exclude: str
    :param local_repo_config_dir: Optional local filesystem path to config directory.
                                  If not provided, config will be cloned from git.
    :type local_repo_config_dir: str
    """

    db = session()
    try:
        repo_config_register = RepoConfigRegister(db, pulp_server)
        repo_config_register.create_repos_from_config(
            regex_include, regex_exclude, local_repo_config_dir
        )
    except Exception:
        log.error(f"unexpected error registering repos for {pulp_server}")
        log.error(traceback.format_exc())
    finally:
        db.close()
