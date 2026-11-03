#!/usr/bin/env python3
"""Build the public app hub from verified public GitHub Pages repositories."""
import argparse
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
import os
import ipaddress
from pathlib import Path
import re
import shutil
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
MAX_HTML = 2_000_000
MAX_ICON = 1_000_000


def public_https_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.username or parsed.password or not host or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        return False
    try:
        if not ipaddress.ip_address(host).is_global:
            return False
    except ValueError:
        if '.' not in host:
            return False
    return True


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_https_url(newurl):
            raise ValueError('Unsafe redirect URL')
        next_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if next_req and urlparse(newurl).hostname != 'api.github.com':
            next_req.remove_header('Authorization')
        return next_req


class Client:
    def __init__(self, token=None):
        self.token = token or ''
        self.opener = build_opener(SafeRedirect())

    def read(self, url, limit=MAX_HTML):
        parsed = urlparse(url)
        if not public_https_url(url):
            raise ValueError('Only public HTTPS resources are supported')
        headers = {'User-Agent': 'Cherryfxt-app-hub', 'Accept': '*/*'}
        if parsed.hostname == 'api.github.com':
            headers.update({'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
            if self.token:
                headers['Authorization'] = 'Bearer ' + self.token
        with self.opener.open(Request(url, headers=headers), timeout=20) as response:
            content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError('Response exceeds configured size limit')
            if response.status != 200:
                raise ValueError('Resource did not return HTTP 200')
            return content, response.headers.get('Content-Type', ''), response.url

    def api(self, path):
        content, _, _ = self.read('https://api.github.com' + path, 8_000_000)
        return json.loads(content)


class PageInfo(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ''
        self.description = ''
        self.theme = ''
        self.icons = []
        self.in_title = False

    def handle_starttag(self, tag, attributes):
        a = {k.lower(): v or '' for k, v in attributes}
        if tag == 'title':
            self.in_title = True
        if tag == 'meta':
            key = (a.get('name') or a.get('property') or '').lower()
            if key in ('description', 'og:description') and not self.description:
                self.description = a.get('content', '').strip()
            if key == 'theme-color':
                self.theme = a.get('content', '').strip()
        if tag == 'link' and a.get('href'):
            rel = a.get('rel', '').lower().split()
            if 'apple-touch-icon' in rel:
                self.icons.insert(0, a['href'])
            elif 'icon' in rel:
                self.icons.append(a['href'])

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def public_repositories(client, owner):
    repos = []
    for page in range(1, 101):
        batch = client.api('/users/' + quote(owner, safe='') + '/repos?type=owner&per_page=100&page=' + str(page))
        if not isinstance(batch, list):
            raise ValueError('Repository discovery returned an invalid response')
        repos.extend(r for r in batch if not r.get('private', False) and r.get('visibility', 'public') == 'public')
        if len(batch) < 100:
            return repos
    raise ValueError('Repository discovery exceeded the pagination limit')


def repo_name(app):
    parts = urlparse(app.get('repo', '')).path.strip('/').split('/')
    return '/'.join(parts[:2]).lower() if len(parts) >= 2 else ''


def pages_url(repo, owner):
    name = repo['name']
    if name.lower() == owner.lower() + '.github.io':
        return 'https://' + owner.lower() + '.github.io/'
    return 'https://' + owner.lower() + '.github.io/' + quote(name, safe='') + '/'


def pages_candidates(client, repo, owner):
    urls = []
    try:
        settings = client.api('/repos/' + quote(repo['full_name'], safe='/') + '/pages')
        candidate = settings.get('html_url') if isinstance(settings, dict) else None
        if candidate and public_https_url(candidate):
            urls.append(candidate)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        pass  # Public repository metadata is still authoritative; Pages settings may require more access.
    urls.append(pages_url(repo, owner))
    homepage = repo.get('homepage')
    # Only consider a declared homepage when the repository already has Pages enabled.
    if homepage and public_https_url(homepage) and urlparse(homepage).hostname not in ('github.com', 'api.github.com'):
        urls.append(homepage)
    return list(dict.fromkeys(urls))


def icon_for(client, info, page_url, slug, output):
    for candidate in info.icons:
        url = urljoin(page_url, candidate)
        p = urlparse(url)
        # An app's icon should come from its own HTTPS origin, never the API or an arbitrary host.
        if p.scheme != 'https' or p.netloc != urlparse(page_url).netloc or p.username or p.password:
            continue
        try:
            data, mime, final_url = client.read(url, MAX_ICON)
            if urlparse(final_url).netloc != p.netloc:
                continue
            mime = mime.split(';')[0].strip().lower()
            extension = {'image/png': '.png', 'image/svg+xml': '.svg', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/x-icon': '.ico', 'image/vnd.microsoft.icon': '.ico'}.get(mime)
            if not extension:
                continue
            rel = Path('assets/discovered') / (slug + extension)
            (output / rel).parent.mkdir(parents=True, exist_ok=True)
            (output / rel).write_bytes(data)
            return rel.as_posix()
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            continue
    return 'assets/fallback-icon.svg'


def discover(client, owner, configured, output, self_repo='app-hub'):
    repos = public_repositories(client, owner)  # Any API failure aborts publication.
    configured_map = {repo_name(a): a for a in configured}
    listed = {r['full_name'].lower(): r for r in repos}
    # Keep manually designed cards on transient Pages failures, while never exposing now-private repos.
    apps = [dict(a) for a in configured if repo_name(a) in listed]
    known = {repo_name(a) for a in apps}
    skipped = 0
    for repo in sorted(repos, key=lambda r: r['name'].lower()):
        key = repo['full_name'].lower()
        if key in known or repo['name'].lower() == self_repo.lower() or not repo.get('has_pages') or repo.get('size', 0) == 0:
            continue
        if 'app-hub-ignore' in repo.get('topics', []):
            continue
        verified = None
        for url in pages_candidates(client, repo, owner):
            try:
                html, mime, final_url = client.read(url)
                if not public_https_url(final_url) or 'html' not in mime.lower():
                    continue
                info = PageInfo()
                info.feed(html.decode('utf-8', errors='replace'))
                verified = (info, final_url)
                break
            except (HTTPError, URLError, TimeoutError, ValueError, OSError):
                continue
        if not verified:
            skipped += 1
            continue
        info, final_url = verified
        slug = re.sub(r'[^a-z0-9_-]+', '-', repo['name'].lower()).strip('-') or str(repo['id'])
        title = re.sub(r'\s+', ' ', unescape(info.title)).strip()[:100] or repo['name']
        description = info.description or repo.get('description') or '打开应用，开始体验。'
        theme = info.theme if re.fullmatch(r'#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?', info.theme) else '#536b59'
        apps.append({'id': 'github-' + str(repo['id']), 'name': title, 'subtitle': 'GitHub Pages 应用', 'category': '其他应用', 'kind': 'discovered', 'url': final_url, 'repo': 'https://github.com/' + repo['full_name'], 'description': str(description)[:240], 'tags': ['应用'], 'icon': icon_for(client, info, final_url, slug, output), 'themeColor': theme})
    return apps, skipped


def build(owner='Cherryfxt', output=None, client=None, root=ROOT):
    output = Path(output or root / '_site').resolve()
    if output == root.resolve() or output in root.resolve().parents:
        raise ValueError('Output must not replace the source repository')
    configured = json.loads((root / 'apps.config.json').read_text())
    if not isinstance(configured, list):
        raise ValueError('apps.config.json must contain a list')
    client = client or Client(os.environ.get('GITHUB_TOKEN'))
    # Stage outside the final output to avoid partially replacing a previously valid build.
    staging = output.parent / (output.name + '-building')
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        for filename in ['index.html', 'style.css', 'app.js', '.nojekyll']:
            shutil.copy2(root / filename, staging / filename)
        shutil.copytree(root / 'assets', staging / 'assets', dirs_exist_ok=True)
        apps, skipped = discover(client, owner, configured, staging)
        if configured and not apps:
            raise ValueError('Discovery unexpectedly removed every configured app; refusing an empty publication')
        (staging / 'apps.json').write_text(json.dumps(apps, ensure_ascii=False, indent=2) + '\n')
        status = {'checkedAt': datetime.now(timezone.utc).isoformat(), 'appCount': len(apps), 'mode': 'automatic', 'interval': 'hourly', 'skippedUnavailable': skipped}
        (staging / 'catalog-status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n')
        if output.exists():
            shutil.rmtree(output)
        staging.rename(output)
        print('Built app hub:', len(apps), 'apps;', skipped, 'unavailable candidate(s) skipped')
        return apps, status
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', default='Cherryfxt')
    parser.add_argument('--output', default=str(ROOT / '_site'))
    args = parser.parse_args()
    try:
        build(args.owner, args.output)
    except Exception as error:
        print('App hub discovery failed:', type(error).__name__, str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
