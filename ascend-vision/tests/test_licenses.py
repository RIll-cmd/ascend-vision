from datetime import datetime
import json

from assistant.licenses import LicenseCheck, added_packages, license_of, manifest_packages, severity

NOW = datetime(2026, 10, 30, 14, 0)


def reader(files):
    return lambda path: files.get(path)


def test_manifest_packages_reads_npm_pip_and_pyproject():
    files = {
        '': 'package.json\nrequirements.txt\nrequirements-dev.txt\npyproject.toml\nREADME.md\n',
        'package.json': json.dumps({'dependencies': {'react': '^18'}, 'devDependencies': {'@types/node': '1'},
                                    'scripts': {'build': 'vite build'}}),
        'requirements.txt': '# pinned\nrequests==2.32\n-r other.txt\n-e .\nhttps://x/y.whl\nNumPy>=1.26 ; python_version>"3"\n',
        'requirements-dev.txt': 'pytest\n',
        'pyproject.toml': '[project]\ndependencies = ["httpx>=0.27"]\n[project.optional-dependencies]\n'
                          'ui = ["rich"]\n[tool.poetry.dependencies]\npython = "^3.11"\nflask = "*"\n',
    }
    assert manifest_packages(reader(files)) == {
        ('npm', 'react'), ('npm', '@types/node'), ('pypi', 'requests'), ('pypi', 'numpy'), ('pypi', 'pytest'),
        ('pypi', 'httpx'), ('pypi', 'rich'), ('pypi', 'flask')}
    assert manifest_packages(reader({'': 'package.json\n', 'package.json': '{broken'})) == set()


def test_added_packages_are_the_ones_on_the_branch_only():
    trees = {'base': {'': 'package.json\n', 'package.json': json.dumps({'dependencies': {'react': '1'}})},
             'room/x': {'': 'package.json\nrequirements.txt\n',
                        'package.json': json.dumps({'dependencies': {'react': '1', 'left-pad': '1'}}),
                        'requirements.txt': 'rich\n'}}

    def run_git(repo, *args):
        if args[0] == 'ls-tree':
            return trees[args[-1]]['']
        ref, _, path = args[1].partition(':')
        return trees[ref].get(path)
    assert added_packages('D:/r', 'base', 'room/x', run_git) == [('npm', 'left-pad'), ('pypi', 'rich')]


def test_license_lookup_on_npm_and_pypi():
    pages = {
        'https://registry.npmjs.org/left-pad/latest': {'license': 'WTFPL'},
        'https://registry.npmjs.org/%40types%2Fnode/latest': {'license': {'type': 'MIT'}},
        'https://pypi.org/pypi/rich/json': {'info': {'license_expression': 'MIT', 'license': 'long text'}},
        'https://pypi.org/pypi/old/json': {'info': {'license': '', 'classifiers': [
            'Programming Language :: Python', 'License :: OSI Approved :: BSD License']}},
        'https://pypi.org/pypi/blank/json': {'info': {'license': None, 'classifiers': []}},
    }
    fetch = pages.get
    assert license_of('npm', 'left-pad', fetch) == 'WTFPL'
    assert license_of('npm', '@types/node', fetch) == 'MIT'
    assert license_of('pypi', 'rich', fetch) == 'MIT'
    assert license_of('pypi', 'old', fetch) == 'BSD License'
    assert license_of('pypi', 'blank', fetch) is None
    assert license_of('pypi', 'missing', fetch) is None


def test_severity_by_license_name():
    for name in ('MIT', 'Apache-2.0', 'BSD-3-Clause', 'ISC', '0BSD', 'Unlicense', 'CC0-1.0', 'Python-2.0', 'Zlib'):
        assert severity(name) == 'low', name
    for name in ('LGPL-3.0', 'MPL-2.0', 'EPL-2.0', 'CC-BY-SA-4.0', 'Some custom license', None):
        assert severity(name) == 'medium', name
    for name in ('GPL-3.0', 'AGPL-3.0-only', 'SSPL-1.0', 'BUSL-1.1', 'UNLICENSED', 'CC-BY-NC-4.0', 'MIT AND Commons Clause'):
        assert severity(name) == 'high', name


def room(tmp_path, sessions):
    for sid, state in sessions.items():
        (tmp_path / 'room' / 'sessions' / sid).mkdir(parents=True)
        (tmp_path / 'room' / 'sessions' / sid / 'state.json').write_text(json.dumps(state))
    return tmp_path / 'room'


def check_for(tmp_path, sessions, added, licenses):
    said = []
    check = LicenseCheck(room(tmp_path, sessions), run_git=None, fetch=None, announce=said.append,
                         folder=tmp_path / 'readings', clock=lambda: NOW, run=lambda job: job(),
                         added=lambda repo, base, branch: added, lookup=lambda eco, name: licenses.get(name))
    return check, said


SESSIONS = {
    '1793000000000-aaaaaa': {'task': 'Old one', 'repo': {'dir': 'D:/r', 'branch': 'room/old', 'base': 'a1'}},
    '1793000000002-cccccc': {'task': 'A debate', 'mode': 'debate'},
    '1793000000001-bbbbbb': {'task': 'Add a date picker\nwith details', 'repo': {'dir': 'D:/r', 'branch': 'room/dp',
                                                                                  'base': 'b2'}},
}


def test_the_newest_room_session_with_a_repo_is_checked_and_risky_ones_named(tmp_path):
    check, said = check_for(tmp_path, SESSIONS, [('npm', 'react-datepicker'), ('npm', 'gpl-thing'), ('pypi', 'x')],
                            {'react-datepicker': 'MIT', 'gpl-thing': 'GPL-3.0'})
    assert check.intercept('Vision, check the licenses') == 'Checking the licenses now.'
    assert said == ["The last Agent Room session, 'Add a date picker', added 3 packages. Two to watch: "
                    "high, gpl-thing uses GPL-3.0; medium, x has no license listed. Notes saved."]
    notes = (tmp_path / 'readings' / '2026-10-30-1400-licenses.md').read_text()
    assert '- **High:** gpl-thing (npm): GPL-3.0' in notes and '- **Low:** react-datepicker (npm): MIT' in notes


def test_all_permissive_or_nothing_added(tmp_path):
    check, said = check_for(tmp_path, SESSIONS, [('npm', 'a'), ('npm', 'b')], {'a': 'MIT', 'b': 'ISC'})
    check.intercept('what licenses did the agents add?')
    assert said[-1] == "The last Agent Room session, 'Add a date picker', added 2 packages, all with permissive licenses."
    check, said = check_for(tmp_path / 'n', SESSIONS, [], {})
    check.intercept('check package licenses')
    assert said[-1] == "The last Agent Room session, 'Add a date picker', added no packages."


def test_no_session_and_unrelated_chat(tmp_path):
    check, said = check_for(tmp_path, {}, [], {})
    assert check.intercept('check the licenses') == "I can't find an Agent Room session with a repo to check."
    assert check.intercept('check the weather') is None


def test_a_git_failure_is_said_plainly(tmp_path):
    said = []

    def broken(repo, base, branch):
        raise OSError('git missing')
    check = LicenseCheck(room(tmp_path, SESSIONS), run_git=None, fetch=None, announce=said.append,
                         folder=tmp_path / 'readings', clock=lambda: NOW, run=lambda job: job(), added=broken,
                         lookup=lambda eco, name: None)
    check.intercept('check the licenses')
    assert said == ["I couldn't read that session's packages."]


# Gap 6 review fixes

def test_a_git_failure_is_never_an_all_clear():
    import pytest
    with pytest.raises(OSError):
        added_packages('D:/r', 'base', 'gone-branch', lambda repo, *args: None)


def test_manifests_anywhere_in_the_tree_count_but_not_vendored_ones():
    trees = {'base': {'': 'README.md\n'},
             'room/x': {'': 'README.md\nascend-vision/requirements.txt\napp/package.json\n'
                            'node_modules/foo/package.json\n.venv/lib/requirements.txt\n',
                        'ascend-vision/requirements.txt': '\ufeffrich\n',
                        'app/package.json': '\ufeff' + json.dumps({'dependencies': {'left-pad': '1'}}),
                        'node_modules/foo/package.json': json.dumps({'dependencies': {'evil': '1'}})}}

    def run_git(repo, *args):
        if args[0] == 'ls-tree':
            assert '-r' in args
            return trees[args[-1]]['']
        ref, _, path = args[1].partition(':')
        return trees[ref].get(path)
    assert added_packages('D:/r', 'base', 'room/x', run_git) == [('npm', 'left-pad'), ('pypi', 'rich')]


def test_gpl_spellings_dual_licenses_and_exceptions():
    for name in ('GPLv3', 'GPLv2+', 'AGPLv3', 'GNU General Public License v3 (GPLv3)',
                 'GNU Affero General Public License v3'):
        assert severity(name) == 'high', name
    assert severity('GNU Lesser General Public License v3 (LGPLv3)') == 'medium'
    assert severity('(MIT OR GPL-3.0)') == 'low'
    assert severity('GPL-2.0-with-classpath-exception') == 'medium'
    assert severity('Apache-2.0 WITH LLVM-exception') == 'low'


def test_many_packages_to_watch_do_not_crash(tmp_path):
    many = [('npm', f'p{n}') for n in range(14)]
    check, said = check_for(tmp_path, SESSIONS, many, {})
    check.intercept('check the licenses')
    assert said[-1].startswith("The last Agent Room session, 'Add a date picker', added 14 packages. 14 to watch:")
    assert '12 more in the notes.' in said[-1]
