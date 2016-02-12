import argparse
import json
import subprocess

import requests


ENDPOINT = 'https://review.gerrithub.io/a'


class GerritClient(object):
    def __init__(self, http_username, http_password):
        self.session = requests.Session()
        self.session.auth = (http_username, http_password)

    def _request(self, method, path, params=None):
        request = requests.Request(
            method,
            ENDPOINT + path,
            params=params)
        prepped = self.session.prepare_request(request)
        print('%s %s' % (prepped.method, prepped.url))
        response = self.session.send(prepped)
        response.raise_for_status()
        return json.loads(response.text.split('\n', 1)[1])

    def get(self, path, params=None):
        return self._request('GET', path, params)


def debug(d):
    print(json.dumps(d, indent=4, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('http_username')
    parser.add_argument('http_password')
    args = parser.parse_args()

    gerrit = GerritClient(args.http_username, args.http_password)

    changes = gerrit.get(
        '/changes/',
        {
            'q': 'is:watched status:open NOT label:Verified>=-1',
            'o': ['LABELS', 'CURRENT_REVISION', 'DOWNLOAD_COMMANDS']
        })
    for change in changes:
        current_revision = change['revisions'][change['current_revision']]
        checkout_cmd = current_revision['fetch']['ssh']['commands']['Checkout']
        repo = current_revision['fetch']['ssh']['url']
        debug(checkout_cmd)
        debug(repo)
