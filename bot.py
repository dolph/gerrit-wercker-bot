import argparse
import errno
import json
import shutil
import subprocess
import tempfile

import pasteraw
import requests


ENDPOINT = 'https://%(host)s/a'


class GerritClient(object):
    def __init__(self, host, username, password):
        self.session = requests.Session()
        self.endpoint = ENDPOINT % {'host': host}
        self.session.auth = (username, password)

    def _request(self, method, path, headers=None, data=None, params=None):
        request = requests.Request(
            method,
            self.endpoint + path,
            params=params,
            headers=headers,
            data=data)
        prepped = self.session.prepare_request(request)
        print('%s %s' % (prepped.method, prepped.url))
        response = self.session.send(prepped)
        response.raise_for_status()
        return json.loads(response.text.split('\n', 1)[1])

    def get(self, path, params=None):
        return self._request('GET', path, params=params)

    def post(self, path, params=None, headers=None, data=None):
        headers = headers or {}
        headers['Content-Type'] = 'application/json'
        if data is not None:
            data = json.dumps(data)
        return self._request(
            'POST', path, params=params, headers=headers, data=data)


def debug(d):
    print(json.dumps(d, indent=4, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('host')
    parser.add_argument('http_username')
    parser.add_argument('http_password')
    args = parser.parse_args()

    gerrit = GerritClient(args.host, args.http_username, args.http_password)

    changes = gerrit.get(
        '/changes/',
        {
            'q': 'is:watched status:open NOT label:Verified owner:dolph',
            'o': ['LABELS', 'CURRENT_REVISION', 'DOWNLOAD_COMMANDS']
        })
    for change in changes:
        current_revision = change['revisions'][change['current_revision']]
        repo = current_revision['fetch']['ssh']['url']
        ref = current_revision['fetch']['ssh']['ref']

        test_commands = [
            ['git', 'clone', repo, '.'],
            ['git', 'fetch', repo, ref],
            ['git', 'checkout', 'FETCH_HEAD'],
            ['git', 'rebase', 'master'],
            ['wercker', 'build'],
        ]

        # Innocent until proven guilty.
        build_succeeded = True

        # Capture stdout and stderr for uploading later.
        output = ''

        try:
            temp_dir = tempfile.mkdtemp()

            for command in test_commands:
                output += '$ %s\n' % ' '.join(command)
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=temp_dir)
                (stdout, stderr) = process.communicate()
                output += stdout
                if process.returncode != 0:
                    build_succeeded = False
                    break
        finally:
            try:
                shutil.rmtree(temp_dir)  # delete directory
            except OSError as exc:
                # Ensure the directory isn't already gone before re-raising.
                if exc.errno != errno.ENOENT:
                    raise

        c = pasteraw.Client()
        url = c.create_paste(output)

        message = 'Build %(status)s: %(url)s' % {
            'status': 'succeeded' if build_succeeded else 'failed',
            'url': url,
        }
        vote = 1 if build_succeeded else -1
        gerrit.post(
            '/changes/%(change_id)s/revisions/%(revision_id)s/review/' % {
                'change_id': change['id'],
                'revision_id': change['current_revision']},
            data={
                'message': message,
                'labels': {
                    'Verified': vote,
                },
            }
        )
