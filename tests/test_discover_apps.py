import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request

spec = importlib.util.spec_from_file_location('discover', Path(__file__).resolve().parent.parent / 'scripts' / 'discover_apps.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def repo(name, **kw):
    return dict(id=hash(name) % 100000, name=name, full_name='Cherryfxt/' + name, has_pages=True, size=1, private=False, **kw)


class FakeClient:
    def __init__(self, repos, pages=None):
        self.repos = repos
        self.pages = pages or {}

    def api(self, path):
        return self.repos

    def read(self, url, limit=d.MAX_HTML):
        if url in self.pages:
            return self.pages[url]
        raise HTTPError(url, 404, 'not found', {}, io.BytesIO())


class DiscoveryTests(unittest.TestCase):
    def test_new_app_and_known_style_preserved(self):
        known = {'id': 'existing', 'name': '人工名称', 'kind': 'english', 'icon': 'assets/original.png', 'repo': 'https://github.com/Cherryfxt/known'}
        repos = [repo('known'), repo('new-app')]
        url = d.pages_url(repos[1], 'Cherryfxt')
        client = FakeClient(repos, {url: (b'<title>New &amp; Nice</title><meta name="description" content="About"><meta name="theme-color" content="#abcd12">', 'text/html', url)})
        with tempfile.TemporaryDirectory() as td:
            apps, skipped = d.discover(client, 'Cherryfxt', [known], Path(td))
        self.assertEqual(apps[0], known)  # Kept even when known app's page is temporarily unavailable.
        self.assertEqual(apps[1]['name'], 'New & Nice')
        self.assertEqual(apps[1]['category'], '其他应用')
        self.assertEqual(apps[1]['themeColor'], '#abcd12')
        self.assertEqual(apps[1]['icon'], 'assets/fallback-icon.svg')
        self.assertEqual(skipped, 0)

    def test_skip_self_private_empty_no_pages_and_404(self):
        repos = [repo('app-hub'), repo('empty'), repo('no-pages'), repo('unavailable'), repo('private')]
        repos[1]['size'] = 0
        repos[2]['has_pages'] = False
        repos[4]['private'] = True
        with tempfile.TemporaryDirectory() as td:
            apps, skipped = d.discover(FakeClient(repos), 'Cherryfxt', [], Path(td))
        self.assertEqual(apps, [])
        self.assertEqual(skipped, 1)

    def test_api_failure_is_not_silenced(self):
        client = Mock()
        client.api.side_effect = HTTPError('https://api.github.com', 403, 'rate limit', {}, io.BytesIO())
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(HTTPError):
                d.discover(client, 'Cherryfxt', [], Path(td))

    def test_token_is_only_sent_to_api(self):
        client = d.Client('secret-test-token')
        requests = []
        class Response:
            status = 200
            headers = {'Content-Type': 'text/html'}
            url = 'https://cherryfxt.github.io/test/'
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return b'ok'
        def open_request(req, timeout):
            requests.append(req)
            return Response()
        client.opener.open = open_request
        client.read('https://api.github.com/users/Cherryfxt/repos')
        client.read('https://cherryfxt.github.io/test/')
        client.read('https://example.com/icon.png')
        self.assertEqual(requests[0].get_header('Authorization'), 'Bearer secret-test-token')
        self.assertIsNone(requests[1].get_header('Authorization'))
        self.assertIsNone(requests[2].get_header('Authorization'))
        req = Request('https://api.github.com/test', headers={'Authorization': 'Bearer secret-test-token'})
        redirected = d.SafeRedirect().redirect_request(req, None, 302, 'Found', {}, 'https://example.com/target')
        self.assertIsNone(redirected.get_header('Authorization'))

    def test_custom_pages_domain_from_api(self):
        item = repo('custom')
        url = 'https://learning.example.com/'
        class CustomClient(FakeClient):
            def api(self, path):
                return {'html_url': url} if path.endswith('/pages') else self.repos
        client = CustomClient([item], {url: (b'<title>Custom Pages</title>', 'text/html', url)})
        with tempfile.TemporaryDirectory() as td:
            apps, skipped = d.discover(client, 'Cherryfxt', [], Path(td))
        self.assertEqual(apps[0]['url'], url)
        self.assertEqual(skipped, 0)

    def test_homepage_fallback_after_default_pages_404(self):
        url = 'https://practice.example.com/'
        item = repo('custom', homepage=url)
        class RestrictedClient(FakeClient):
            def api(self, path):
                if path.endswith('/pages'):
                    raise HTTPError('https://api.github.com' + path, 403, 'forbidden', {}, io.BytesIO())
                return self.repos
        client = RestrictedClient([item], {url: (b'<title>Practice</title>', 'text/html', url)})
        with tempfile.TemporaryDirectory() as td:
            apps, skipped = d.discover(client, 'Cherryfxt', [], Path(td))
        self.assertEqual(apps[0]['url'], url)
        self.assertEqual(skipped, 0)

    def test_build_writes_only_runtime_and_generated_catalog(self):
        item = repo('example')
        url = d.pages_url(item, 'Cherryfxt')
        client = FakeClient([item], {url: (b'<title>Example App</title>', 'text/html', url)})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'apps.config.json').write_text('[]')
            for f in ['index.html', 'app.js', 'style.css', '.nojekyll']:
                (root / f).write_text('site asset')
            (root / 'assets').mkdir()
            (root / 'assets/fallback-icon.svg').write_text('<svg/>')
            output = root / '_site'
            apps, status = d.build(client=client, root=root, output=output)
            self.assertEqual(status['appCount'], 1)
            self.assertEqual(status['interval'], 'hourly')
            self.assertEqual(json.loads((output / 'apps.json').read_text()), apps)
            self.assertEqual(json.loads((output / 'catalog-status.json').read_text()), status)
            self.assertFalse((output / 'apps.config.json').exists())
            self.assertEqual((output / 'assets/fallback-icon.svg').read_text(), '<svg/>')

    def test_failed_build_preserves_last_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / '_site'
            output.mkdir()
            (output / 'apps.json').write_text('last-good-output')
            (root / 'apps.config.json').write_text('[]')
            for f in ['index.html', 'app.js', 'style.css', '.nojekyll']:
                (root / f).write_text('')
            (root / 'assets').mkdir()
            client = Mock()
            client.api.side_effect = ValueError('GitHub API unavailable')
            with self.assertRaises(ValueError):
                d.build(client=client, root=root, output=output)
            self.assertEqual((output / 'apps.json').read_text(), 'last-good-output')
            self.assertFalse((root / '_site-building').exists())

    def test_local_or_non_https_icon_does_not_fetch(self):
        info = d.PageInfo()
        info.feed('<link rel="icon" href="http://127.0.0.1/secret"><link rel="icon" href="https://api.github.com/private">')
        client = Mock()
        with tempfile.TemporaryDirectory() as td:
            path = d.icon_for(client, info, 'https://cherryfxt.github.io/new/', 'new', Path(td))
        self.assertEqual(path, 'assets/fallback-icon.svg')
        client.read.assert_not_called()
        self.assertFalse(d.public_https_url('https://127.0.0.1/secret'))

    def test_icon_relative_resolution(self):
        url = 'https://cherryfxt.github.io/new/'
        info = d.PageInfo()
        info.feed('<link rel="apple-touch-icon" href="./icons/app.png">')
        client = FakeClient([], {url + 'icons/app.png': (b'png-data', 'image/png', url + 'icons/app.png')})
        with tempfile.TemporaryDirectory() as td:
            target = d.icon_for(client, info, url, 'new', Path(td))
            self.assertEqual(target, 'assets/discovered/new.png')
            self.assertEqual((Path(td) / target).read_bytes(), b'png-data')


if __name__ == '__main__':
    unittest.main()
