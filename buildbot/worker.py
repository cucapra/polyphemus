import threading
import subprocess
import os
from .db import ARCHIVE_NAME, CODE_DIR
from contextlib import contextmanager
import traceback
import shlex

SEASHELL_EXT = '.ss'
C_EXT = '.c'


class WorkError(Exception):
    """An error that occurs in a worker that needs to be displayed in
    the log.
    """
    def __init__(self, message):
        self.message = message


@contextmanager
def work(db, old_state, temp_state, done_state):
    """A context manager for acquiring a job temporarily in an
    exclusive way to work on it.
    """
    job = db.acquire(old_state, temp_state)
    try:
        yield job
    except WorkError as exc:
        db._log(job, exc.message)
        db.set_state(job, 'failed')
    except Exception:
        db._log(job, traceback.format_exc())
        db.set_state(job, 'failed')
    else:
        db.set_state(job, done_state)


def run(cmd, **kwargs):
    """Run a command, like `subprocess.run`, while capturing output. Log an
    appropriate error if the command fails.

    `cmd` must be a list of arguments.
    """
    # A string representation of the command for logging.
    cmd_str = ' '.join(shlex.quote(p) for p in cmd)

    try:
        return subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            **kwargs
        )
    except subprocess.CalledProcessError as exc:
        raise WorkError('command failed: {} (code {}):\n{}'.format(
            cmd_str,
            exc.returncode,
            '\n---\n'.join(filter(lambda x: x, (
                exc.stdout.decode('utf8', 'ignore'),
                exc.stderr.decode('utf8', 'ignore'),
            )))
        ))


class WorkThread(threading.Thread):
    """A base class for all our worker threads, which run indefinitely
    to process tasks in an appropriate state.
    """

    def __init__(self, db, config):
        self.db = db
        self.config = config
        super(WorkThread, self).__init__(daemon=True)

    def run(self):
        while True:
            self.work()


class UnpackThread(WorkThread):
    """Unpack source code.
    """
    def work(self):
        with work(self.db, 'uploaded', 'unpacking', 'unpacked') as job:
            proc = subprocess.run(
                ["unzip", "-d", CODE_DIR, "{}.zip".format(ARCHIVE_NAME)],
                cwd=self.db.job_dir(job['name']),
                check=True,
                capture_output=True,
            )
            self.db._log(job, proc.stdout.decode('utf8', 'ignore'))


class SeashellThread(WorkThread):
    """Compile Seashell code to HLS.
    """
    def work(self):
        compiler = self.config["SEASHELL_COMPILER"]
        with work(self.db, 'unpacked', 'seashelling', 'seashelled') as job:
            # Look for the Seashell source code.
            code_dir = os.path.join(self.db.job_dir(job['name']), CODE_DIR)
            for name in os.listdir(code_dir):
                _, ext = os.path.splitext(name)
                if ext == SEASHELL_EXT:
                    source_name = name
                    break
            else:
                raise WorkError('no source file found')
            job['seashell_main'] = name

            # Read the source code.
            with open(os.path.join(code_dir, source_name), 'rb') as f:
                code = f.read()

            # Run the Seashell compiler.
            proc = run(compiler, input=code)
            if proc.stderr:
                self.db._log(job, proc.stderr.decode('utf8', 'ignore'))
            hls_code = proc.stdout

            # A filename for the translated C code.
            base, _ = os.path.splitext(source_name)
            c_name = base + C_EXT
            job['c_main'] = c_name

            # Write the C code.
            with open(os.path.join(code_dir, c_name), 'wb') as f:
                f.write(hls_code)


def work_threads(db, config):
    """Get a list of (unstarted) Thread objects for processing tasks.
    """
    return [
        UnpackThread(db, config),
        SeashellThread(db, config),
    ]
