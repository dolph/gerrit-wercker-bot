import argparse
import errno
import json
import logging
import shutil
import subprocess
import tempfile
import time

import pasteraw
import requests


LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)

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
        LOG.debug('%s %s' % (prepped.method, prepped.url))
        response = self.session.send(prepped)
        response.raise_for_status()
        return json.loads(response.text.split('\n', 1)[1])

    def get(self, path, params=None):
        return self._request('GET', path, params=params)

    def post(self, path, params=None, headers=None, data=None):
        headers = headers or {}
        if data is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(data)
        return self._request(
            'POST', path, params=params, headers=headers, data=data)


def debug(d):
    print(json.dumps(d, indent=4, sort_keys=True))


def test_change(change):
    current_revision = change['revisions'][change['current_revision']]
    repo = current_revision['fetch']['ssh']['url']
    ref = current_revision['fetch']['ssh']['ref']

    LOG.info('Testing %s %s' % (repo, ref))

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
    output = b''

    # Capture the runtime of all shell commands.
    start_time = time.time()

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
            output += stdout.encode('utf-8')

            if process.returncode != 0:
                build_succeeded = False
                break
    except Exception as e:
        build_succeeded = False
        output += e
    finally:
        try:
            shutil.rmtree(temp_dir)  # delete directory
        except OSError as exc:
            # Ensure the directory isn't already gone before re-raising.
            if exc.errno != errno.ENOENT:
                raise

    # Calculate total elapsed runtime of the build (in seconds).
    elapsed_seconds = round(time.time() - start_time)

    c = pasteraw.Client()
    url = c.create_paste(output)

    message = 'Build %(status)s (%(min)dm %(sec)ds): %(url)s' % {
        'status': 'succeeded' if build_succeeded else 'failed',
        'min': elapsed_seconds / 60,
        'sec': elapsed_seconds % 60,
        'url': url,
    }
    vote = 1 if build_succeeded else -1

    LOG.info(message)

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

    return build_succeeded


def main(gerrit):
    changes = gerrit.get(
        '/changes/',
        {
            'q': 'is:watched status:open NOT label:Verified>=-1',
            'o': ['LABELS', 'CURRENT_REVISION', 'DOWNLOAD_COMMANDS']
        })
    if changes:
        try:
            test_change(changes[-1])
        except Exception as e:
            LOG.exception('Exception testing change.')

    # Merge any changes that are ready.
    changes = gerrit.get(
        '/changes/',
        {
            'q': 'is:watched status:open label:Verified+1 label:Code-Review+2',
            'o': ['LABELS', 'CURRENT_REVISION', 'DOWNLOAD_COMMANDS']
        })
    merge_path = '/changes/%(change_id)s/revisions/%(revision_id)s/submit'
    if changes:
        changes.reverse()
    for change in changes:
        if not change['submittable']:
            continue

        LOG.info(change)

        try:
            build_succeeded = test_change(change)
        except Exception as e:
            LOG.exception('Exception testing change.')
            continue

        if build_succeeded:
            LOG.info('Merging %s' % change['id'])
            try:
                gerrit.post(
                    merge_path % {
                        'change_id': change['id'],
                        'revision_id': change['current_revision']},
                )
            except requests.HTTPError as e:
                LOG.info('Failed to merge due to %s %s' % (
                    e.response.status_code, e.response.reason))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('host')
    parser.add_argument('http_username')
    parser.add_argument('http_password')
    args = parser.parse_args()

    gerrit = GerritClient(args.host, args.http_username, args.http_password)

    try:
        while True:
            try:
                main(gerrit)
            except Exception as e:
                LOG.exception('Exception in main loop, sleeping and retrying.')
            time.sleep(60)
    except KeyboardInterrupt:
        pass
