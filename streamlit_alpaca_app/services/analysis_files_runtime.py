"""Container-side runtime. This file alone is mounted; no application or keys."""
from __future__ import annotations
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import sys
import time
import traceback
import uuid


@contextlib.contextmanager
def edit_workbook(filename):
    """Edit through native UNO; macros and linked-document updates disabled."""
    import uno
    from com.sun.star.beans import PropertyValue
    from com.sun.star.document.MacroExecMode import NEVER_EXECUTE
    from com.sun.star.document.UpdateDocMode import NO_UPDATE
    def property(name, value):
        p = PropertyValue(); p.Name = name; p.Value = value
        return p
    path = Path(filename).resolve()
    if not path.is_relative_to(Path('/workspace')):
        raise ValueError("Recalculation requires a writable workspace copy")
    pipe = 'analysis_' + uuid.uuid4().hex
    process = subprocess.Popen(['soffice', '--headless', '--nologo', '--nodefault',
        '--nofirststartwizard', '-env:UserInstallation=file:///tmp/' + pipe,
        '--accept=pipe,name=' + pipe + ';urp;StarOffice.ComponentContext'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    desktop = document = None
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', local)
        for _ in range(100):
            try:
                remote = resolver.resolve('uno:pipe,name=' + pipe + ';urp;StarOffice.ComponentContext')
                break
            except Exception:
                if process.poll() is not None:
                    raise RuntimeError('Native workbook engine exited during startup')
                time.sleep(.1)
        else:
            raise TimeoutError('Native workbook engine startup timed out')
        desktop = remote.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', remote)
        document = desktop.loadComponentFromURL(uno.systemPathToFileUrl(str(path)), '_blank', 0,
            (property('Hidden', True), property('ReadOnly', False),
             property('MacroExecutionMode', NEVER_EXECUTE), property('UpdateDocMode', NO_UPDATE)))
        if document is None:
            raise ValueError('Native workbook engine could not open file')
        yield document
        document.calculateAll()
        document.store()
    finally:
        if document is not None:
            with contextlib.suppress(Exception):
                document.dispose()
        if desktop is not None:
            with contextlib.suppress(Exception):
                desktop.terminate()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()


def recalculate_workbook(filename):
    with edit_workbook(filename):
        pass
    return filename


def run():
    request = json.load(sys.stdin)
    Path('/workspace/output').mkdir(exist_ok=True)
    Path('/workspace/home').mkdir(exist_ok=True)
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024,) * 2)
    output, errors = io.StringIO(), io.StringIO()
    result = {'status': 'completed'}
    namespace = {'__name__': '__main__', 'recalculate_workbook': recalculate_workbook,
                 'edit_workbook': edit_workbook,
                 'input_files': {r['name']: '/input/' + r['name'] for r in request['inputs']}}
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            exec(compile(request['code'], '<analysis>', 'exec'), namespace)
    except BaseException as exc:
        result.update(status='failed', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
    for name, stream in [('stdout', output), ('stderr', errors)]:
        text = stream.getvalue()
        result[name] = text[:100_000]
        result[name + '_truncated'] = len(text) > 100_000
    sys.__stdout__.write(json.dumps(result))


def export():
    root = Path('/workspace')
    files, total = [], 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        if Path(directory) == root:
            dirs[:] = [name for name in dirs if name != 'home']
        for name in dirs + names:
            if (Path(directory) / name).is_symlink():
                raise ValueError('Output symlinks are not file artifacts')
        for name in sorted(names):
            path = Path(directory) / name
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError('Only regular files can be exported')
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    data = stream.read(64 * 1024 * 1024 + 1)
            finally:
                os.close(fd)
            total += len(data)
            if len(files) >= 256 or total > 64 * 1024 * 1024:
                raise ValueError('Working checkpoint exceeds256files or64MiB')
            files.append({'name': str(path.relative_to(root)), 'base64': base64.b64encode(data).decode()})
    if sum(item['name'].startswith('output/') for item in files) > 24:
        raise ValueError('Output exceeds24files')
    print(json.dumps(files))


if __name__ == '__main__':
    {'run': run, 'export': export}[sys.argv[1]]()
