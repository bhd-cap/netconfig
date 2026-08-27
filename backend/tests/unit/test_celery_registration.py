"""
Every task an endpoint queues has to be registered on the worker.

A .delay() for a task the worker does not know about does not fail at the API:
the message goes onto the queue and the worker logs "Received unregistered
task" and drops it. That is invisible from the caller's side, so it is worth a
test rather than a comment.
"""
import pkgutil

import pytest

import app.tasks
from app.celery_app import celery_app


@pytest.fixture(scope="module", autouse=True)
def worker_startup():
    """
    Import the modules in the include list, as a starting worker does

    Celery imports them lazily, so without this celery_app.tasks holds only
    the built-ins and the test would pass for the wrong reason.
    """
    celery_app.loader.import_default_modules()


# The tasks an HTTP request can enqueue, by the name they are registered under.
QUEUED_BY_THE_API = [
    "app.tasks.backup.backup_device_task",
    "app.tasks.backup.bulk_backup_task",
    "app.tasks.backup.scheduled_backup_task",
    "app.tasks.discovery.discovery_crawl_task",
    "app.tasks.discovery.refresh_inventory_task",
    "app.tasks.remote_backup.upload_to_target_task",
    "app.tasks.remote_backup.export_new_configurations_task",
]


@pytest.mark.parametrize("name", QUEUED_BY_THE_API)
def test_task_is_registered(name):
    assert name in celery_app.tasks, (
        f"{name} is not registered. Add its module to the include list in "
        f"app/celery_app.py."
    )


def test_every_beat_entry_points_at_a_registered_task():
    for entry, config in celery_app.conf.beat_schedule.items():
        assert config["task"] in celery_app.tasks, (
            f"Beat entry '{entry}' schedules {config['task']}, which is not "
            f"registered."
        )


def test_every_task_module_is_included():
    """
    No task module may rely on another importing it

    A module left out of the include list registers only as a side effect of
    some other import, which makes removing that import a silent breakage.
    """
    on_disk = {
        f"app.tasks.{module.name}"
        for module in pkgutil.iter_modules(app.tasks.__path__)
        if not module.name.startswith("_")
    }

    included = set(celery_app.conf.include or [])

    assert on_disk <= included, (
        f"Task module(s) missing from the include list in app/celery_app.py: "
        f"{sorted(on_disk - included)}"
    )
