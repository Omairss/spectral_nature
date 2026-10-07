"""Apply only the accepted lock fix to each immutable deployed image.

All unrelated functions and dependencies remain those of the original image.
This build-time tool is not copied into the resulting runtime image.
"""
import ast
from pathlib import Path
import py_compile

ROOT = Path('/app')
REFERENCE = Path('/lock-release/reference')


def function(source, name):
    matches = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(matches) == 1, (name, len(matches))
    return matches[0]


def replace_node(source, node, text):
    lines = source.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + text.rstrip() + '\n' + ''.join(lines[node.end_lineno:])


def node_text(source, node):
    return '\n'.join(source.splitlines()[node.lineno - 1:node.end_lineno])


def save(relative, source):
    path = ROOT / relative
    ast.parse(source)
    path.write_text(source)
    py_compile.compile(str(path), doraise=True)


relative = 'services/saa/zopedia.py'
original = (ROOT / relative).read_text()
accepted = (REFERENCE / relative).read_text()
ready = node_text(accepted, function(accepted, '_zopedia_storage_ready'))
old_ready = [n for n in ast.parse(original).body if isinstance(n, ast.FunctionDef) and n.name == '_zopedia_storage_ready']
if old_ready:
    original = replace_node(original, old_ready[0], ready)
else:
    original += '\n\n' + ready + '\n'
constants = [n for n in ast.parse(original).body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == '_ZOPEDIA_REQUIRED_RELATIONS']
accepted_constant = next(n for n in ast.parse(accepted).body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == '_ZOPEDIA_REQUIRED_RELATIONS')
if constants:
    original = replace_node(original, constants[0], node_text(accepted, accepted_constant))
else:
    original += '\n' + node_text(accepted, accepted_constant) + '\n'
bootstrap = function(original, 'bootstrap_zopedia_storage')
if not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == '_zopedia_storage_ready' for n in ast.walk(bootstrap)):
    lines = original.splitlines(keepends=True)
    lines.insert(bootstrap.body[0].lineno - 1, '    if _zopedia_storage_ready(conn):\n        return\n')
    original = ''.join(lines)
save(relative, original)

relative = 'services/saa/storage.py'
original = (ROOT / relative).read_text()
accepted = (REFERENCE / relative).read_text()
accepted_bootstrap = function(accepted, 'bootstrap_saa_storage')
guard_end = next(n.end_lineno for n in accepted_bootstrap.body if isinstance(n, ast.If) and isinstance(n.test, ast.BoolOp) and any(isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == '_zopedia_storage_ready' for x in ast.walk(n.test)))
guard = '\n'.join(accepted.splitlines()[accepted_bootstrap.body[0].lineno - 1:guard_end]) + '\n'
bootstrap = function(original, 'bootstrap_saa_storage')
lines = original.splitlines(keepends=True)
lines.insert(bootstrap.body[0].lineno - 1, guard)
save(relative, ''.join(lines))

relative = 'pipeline/jobs/main.py'
source = (ROOT / relative).read_text()
connection = function(source, '_db_connection')
calls = [n for n in ast.walk(connection) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id == 'psycopg' and n.func.attr == 'connect']
assert len(calls) == 1
call = calls[0]
keywords = {k.arg for k in call.keywords}
assert 'application_name' not in keywords and 'options' not in keywords
call.keywords.extend([
    ast.keyword(arg='application_name', value=ast.parse("f\"spectral_pipeline:{(os.getenv('PIPELINE_JOB_NAME') or 'unknown')[:45]}\"", mode='eval').body),
    ast.keyword(arg='options', value=ast.Constant('-c lock_timeout=5000 -c statement_timeout=120000 -c idle_in_transaction_session_timeout=60000')),
])
lines = source.splitlines(keepends=True)
start = sum(map(len, lines[:call.lineno - 1])) + call.col_offset
end = sum(map(len, lines[:call.end_lineno - 1])) + call.end_col_offset
source = source[:start] + ast.unparse(call) + source[end:]
news = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == '_zopedia_pages_for_news_resolution']
changed = 0
if news:
    shared_calls = [n for n in ast.walk(news[0]) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'list_zopedia_pages']
    for call in sorted(shared_calls, key=lambda n:n.lineno, reverse=True):
        if call.args:
            assert len(call.args) == 1 and isinstance(call.args[0], ast.Name) and call.args[0].id == 'conn'
            call.args = []
            changed += 1
        if any(k.arg == 'conn' for k in call.keywords):
            call.keywords = [k for k in call.keywords if k.arg != 'conn']
            changed += 1
        lines = source.splitlines(keepends=True)
        start = sum(map(len, lines[:call.lineno - 1])) + call.col_offset
        end = sum(map(len, lines[:call.end_lineno - 1])) + call.end_col_offset
        source = source[:start] + ast.unparse(call) + source[end:]
save(relative, source)
print('LOCK_FIX_BACKPORT', {'schema_guards':2, 'bounded_tracking_connection':True, 'shared_news_reads_removed':changed})
