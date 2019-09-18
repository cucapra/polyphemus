import os
import re
import shlex
import subprocess
import traceback

from . import state
from .db import ARCHIVE_NAME, CODE_DIR
from contextlib import contextmanager

def _cmd_str(cmd):
    """Given a list of command-line arguments, return a human-readable
    string for logging.
    """
    return ' '.join(shlex.quote(p) for p in cmd)

class WorkError(Exception):
    """An error that occurs in a worker that needs to be displayed in
    the log.
    """
    def __init__(self, message):
        self.message = message

class JobTask:
    """A temporary acquisition of a job used to do a single unit of work
    on its behalf. Also, a container for lots of convenience methods for
    doing stuff with the job.

    Subscripting a task accesses the underlying job dict.
    """
    def __init__(self, db, job):
        self.db = db
        self.job = job

    def __getitem__(self, key):
        return self.job[key]

    def __setitem__(self, key, value):
        self.job[key] = value

    @property
    def dir(self):
        """The path the directory containing the job's files.
        """
        return self.db.job_dir(self.job['name'])

    @property
    def code_dir(self):
        """The path the job's directory for code and compilation.
        """
        return os.path.join(self.dir, CODE_DIR)

    def log(self, message):
        """Add an entry to the job's log.
        """
        self.db.log(self.job['name'], message)

    def set_state(self, state):
        """Set the job's state.
        """
        self.db.set_state(self.job, state)

    def run(self, cmd, capture=False, timeout=60, cwd='', **kwargs):
        """Run a command and log its output.

        Return an exited process object. If `capture`, then  the
        standard output is *not* logged and is instead available as the return
        value's `stdout` field. Additional arguments are forwarded to
        `subprocess.run`.

        Raise an appropriate `WorkError` if the command fails.
        """
        full_cwd = os.path.normpath(os.path.join(self.dir, cwd))
        self.log('$ {}'.format(_cmd_str(cmd)))

        log_filename = self.db._log_path(self.job['name'])
        with open(log_filename, 'ab') as f:
            try:
                return subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.PIPE if capture else f,
                    stderr=f,
                    timeout=timeout,
                    cwd=full_cwd,
                    **kwargs,
                )
            except subprocess.CalledProcessError as exc:
                raise WorkError('command failed ({})'.format(
                    exc.returncode,
                ))
            except FileNotFoundError as exc:
                raise WorkError('command {} not found'.format(
                    exc.filename,
                ))
            except subprocess.TimeoutExpired as exc:
                raise WorkError('timeout after {} seconds'.format(
                    exc.timeout,
                ))


@contextmanager
def work(db, old_state, temp_state, done_state_or_func):
    """A context manager for acquiring a job temporarily in an
    exclusive way to work on it. Produce a `JobTask`.
    Done state can either be a valid state string or a function that
    accepts a Task object and returns a valid state string.
    """
    done_func = None
    if isinstance(done_state_or_func, str):
        done_func = lambda _: done_state_or_func  # noqa
    else:
        done_func = done_state_or_func

    job = db.acquire(old_state, temp_state)
    task = JobTask(db, job)
    try:
        yield task
    except WorkError as exc:
        task.log(exc.message)
        task.set_state(state.FAIL)
    except Exception:
        task.log(traceback.format_exc())
        task.set_state(state.FAIL)
    else:
        task.set_state(done_func(task))


def task_config(task, config):
    """Interpret some configuration options on a task, and assign the
    `platform`, `estimate`, etc. fields so they can be used
    directly.
    """
    task['estimate'] = int(task['config'].get('estimate'))

    task['platform'] = task['config'].get('platform') or \
        config['DEFAULT_PLATFORM']
    task['mode'] = task['config'].get('mode') or \
        config['DEFAULT_F1_MODE']


def update_make_conf(make_cmd, task, db, config):
    """Extract configuration variables from a make job and update the config
    object with them.
    """

    # Before running the make target, collect configuration information.
    proc = task.run(make_cmd + ['--dry-run', '--print-data-base'],
                    capture=True, cwd=CODE_DIR)
    log = proc.stdout.decode('utf8').strip()

    # Extract relevant conf options.
    conf_str = r"^\s*({})\s*:?=\s*(.*)$".format('|'.join(config['MAKE_CONF_VARS']))
    conf_re = re.compile(conf_str, re.I)

    make_conf = {}
    for line in log.split('\n'):
        matches = conf_re.search(line.strip())
        if matches:
            make_conf[matches.group(1)] = matches.group(2)

    # Update the job config with make_conf
    task.job['config']['make_conf'] = make_conf
    db.log(task.job['name'], 'make conf added {}'.format(make_conf))
    db._write(task.job)
