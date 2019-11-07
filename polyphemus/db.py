import threading
import secrets
import time
import os
from contextlib import contextmanager
import json
from datetime import datetime
from collections import defaultdict
import random

from . import state

JOBS_DIR = 'jobs'
ARCHIVE_NAME = 'code'
CODE_DIR = 'code'
INFO_FILENAME = 'info.json'
LOG_FILENAME = 'log.txt'


@contextmanager
def chdir(path):
    """Temporarily change the working directory (then change back).
    """
    old_dir = os.getcwd()
    os.chdir(path)
    yield
    os.chdir(old_dir)


class NotFoundError(Exception):
    """The job indicated could not be found.
    """
    pass


class BadJobError(Exception):
    """The requested job is corrupted and unusable.
    """


class JobDB:
    """A wrapper around the jobs directory. Worker threads use this to
    acquire potential jobs and move them along the state transition graph.
    """
    def __init__(self, base_path):
        self.base_path = base_path
        os.makedirs(self.base_path, exist_ok=True)
        os.makedirs(os.path.join(self.base_path, JOBS_DIR), exist_ok=True)

        # Mapping from job_id to its state
        self.job_cache = {}

        # Lock for the cache.
        self.cache_lock = threading.Condition()

        # Lock for the DB.
        self.cv = threading.Condition()

    def job_dir(self, job_name):
        """Get the path to a job's work directory.
        """
        return os.path.join(self.base_path, JOBS_DIR, job_name)

    def _info_path(self, name):
        """Get the path to a job's info JSON file."""
        return os.path.join(self.job_dir(name), INFO_FILENAME)

    def _log_path(self, name):
        """Get the path to a job's log file."""
        return os.path.join(self.job_dir(name), LOG_FILENAME)

    def _read(self, name):
        """Read a job from its info file.

        Raise a NotFoundError if there is no such job.
        """
        path = self._info_path(name)
        if os.path.isfile(path):
            with open(path) as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    raise BadJobError()
        else:
            raise NotFoundError()

    def _write(self, job):
        """Write a job back to its info file.
        """
        with open(self._info_path(job['name']), 'w') as f:
            json.dump(job, f)

    def _all(self, with_cache=False):
        """Read all the jobs.

        Corrupted/unreadable jobs are not included in the list. This is
        probably pretty slow, and it's O(n) where n is the total number
        of jobs in the system.

        When `with_cache` is True, prioritize returning jobs that are not
        in a done state in the cache.
        """
        traversal = list(os.listdir(os.path.join(self.base_path, JOBS_DIR)))
        random.shuffle(traversal)

        for name in traversal:
            if with_cache and self.job_cache.get(name) in [state.DONE, state.FAIL]:
                continue

            path = self._info_path(name)
            if os.path.isfile(path):
                with open(path) as f:
                    try:
                        yield json.load(f)
                    except json.JSONDecodeError:
                        continue

        if with_cache:
            for job, job_state in self.job_cache.items():
                if job_state in [state.DONE, state.FAIL]:
                    path = self._info_path(name)
                    if os.path.isfile(path):
                        with open(path) as f:
                            try:
                                yield json.load(f)
                            except json.JSONDecodeError:
                                continue


    def _acquire(self, old_state, new_state):
        """Look for a job in `old_state`, update it to `new_state`, and
        return it.

        First check the job_cache for matching jobs and test if they are still
        in that state. If no job is found in that state, call _all.

        Raise a `NotFoundError` if there is no such job.
        """

        job = None

        # Try to find a job in old_state in the job cache.
        for job_id, state in self.job_cache.items():
            # If the cache state doesn't match old_state, skip the job
            if state != old_state:
                continue

            # Cache can contain old state data. Get the actual data by reading
            # the db.
            job = self._read(job_id)
            if job['state'] == old_state:
                break
            else:
                # If the cached data was wrong, update the cache.
                with self.cache_lock:
                    self.job_cache[job_id] = job['state']


        # If there are no matching jobs in the cache, walk the jobs dir.
        if job is None:
            for job in self._all(with_cache=True):
                if job['state'] == old_state:
                    break
                else:
                    with self.cache_lock:
                        self.job_cache[job['name']] = job['state']
            else:
                raise NotFoundError()

        job['state'] = new_state

        with self.cache_lock:
            self.job_cache[job['name']] = job['state']

        self.log(job['name'], 'acquired in state {}'.format(new_state))
        print(job['name'], 'acquired in state {}. Cache size: {}.'.format(new_state, len(self.job_cache)))
        with open(self._info_path(job['name']), 'w') as f:
            json.dump(job, f)

        return job

    def _init(self, name, state, config):
        """Given the name of a job *whose directory already exists*,
        initialize with a database entry. In other words, create the job
        for existing on-disk job-related files. Return the new job.
        """
        job = {
            'name': name,
            'started': time.time(),
            'state': state,
            'config': config,
        }
        self._write(job)
        return job

    def _gen_name(self):
        """Generate a new, random job name.
        """
        return secrets.token_urlsafe(8).replace('-', '_')

    def _add(self, state, config):
        name = self._gen_name()
        os.mkdir(self.job_dir(name))
        job = self._init(name, state, config)
        return job

    def add(self, state, config={}):
        """Add a new job and return it.
        """
        with self.cv:
            job = self._add(state, config)
            self.cv.notify_all()
        return job

    def log(self, name, message):
        """Add a message to the named job's log.
        """
        fn = self._log_path(name)
        timestamp = datetime.now().isoformat()
        with open(fn, 'a') as f:
            print(timestamp, message, file=f)

    @contextmanager
    def create(self, state, config={}):
        """A context manager for creating a new job. A directory is
        created for the job, and the working directory is temporarily
        changed there, and *then* the job is initialized in the given
        state. The context gets the new job's name.
        """
        name = self._gen_name()
        job_dir = self.job_dir(name)
        os.mkdir(job_dir)
        with chdir(job_dir):
            yield name
        with self.cv:
            self._init(name, state, config)
            self.cv.notify_all()

    def set_state(self, job, state):
        """Update a job's state.
        """
        with self.cv:
            job['state'] = state
            self.log(job['name'], 'state changed to {}'.format(state))
            self._write(job)
            self.cv.notify_all()

    def acquire(self, old_state, new_state):
        """Block until a job is available in `old_state`, update its
        state to `new_state`, and return it.
        """
        with self.cv:
            while True:
                try:
                    job = self._acquire(old_state, new_state)
                except NotFoundError:
                    pass
                else:
                    break
                self.cv.wait()
            return job

    def get(self, name):
        """Get the job with the given name.
        """
        with self.cv:
            return self._read(name)
