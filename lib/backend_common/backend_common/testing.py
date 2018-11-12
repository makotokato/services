# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import base64
import collections
import datetime
import hashlib
import json
import os
import random
import re
import time
import urllib.parse

import pytest
import responses

# Personal data generated by 'faker' module, any resemblance to
# real people is purely coincidental.
AUTH0_DUMMY_USERINFO = {
    'family_name': 'Moran',
    'given_name': 'Lydia',
    'nickname': 'Lydia Moran',
    'groups': [
        'avengers',
        'JusticeLeague',
        'x_men',
        'the_specials',
        'fantastic4'
    ],
    'emails': ['lmoran@mozilla.com'],
    'dn': 'mail=lmoran@mozilla.com,o=com,dc=mozilla',
    'organizationUnits': 'mail=lmoran@mozilla.com,o=com,dc=mozilla',
    'email': 'lmoran@mozilla.com',
    'name': 'Lydia Moran',
    'picture': 'http://people.mozilla.com/~faaborg/files/shiretoko/firefoxIcon/firefox-128.png',
    'email_verified': True,
    'clientID': 'abcdefghijklmnopqrstuvwxyz123456',
    'updated_at': '2017-04-25T09:36:57.950Z',
    'user_id': 'ad|Mozilla-LDAP|lmoran',
    'identities': [
        {
            'user_id': 'Mozilla-LDAP|lmoran',
            'provider': 'ad',
            'connection': 'Mozilla-LDAP',
            'isSocial': False
        }
    ],
    'created_at': '2017-03-07T10:14:51.077Z',
    'multifactor': ['duo'],
    'sub': 'ad|Mozilla-LDAP|lmoran'
}


def get_app_config(extra_config):
    config = {
        'TESTING': True,
        'SECRET_KEY': os.urandom(24)
    }
    config.update(extra_config)
    return config


# TODO: move this to cli_common and mock taskcluster module
requests_mock = responses.RequestsMock(assert_all_requests_are_fired=False)


def build_header(client_id, ext_data=None):
    '''Build a fake Hawk header to share client id & scopes.
    '''

    out = collections.OrderedDict({
        'id': client_id,
        'ts': int(time.time()),
        'nonce': random.randint(0, 100000),
    })
    if ext_data is not None:
        json_data = json.dumps(ext_data, sort_keys=True).encode('utf-8')
        out['ext'] = base64.b64encode(json_data).decode('utf-8')

    mac_contents = '\n'.join(map(str, out.values()))
    out['mac'] = hashlib.sha1(mac_contents.encode('utf-8')).hexdigest()

    parts = map(lambda x: '{}="{}"'.format(*x), out.items())
    return 'Hawk {}'.format(', '.join(parts))


def parse_header(header):
    '''Parse a fake Hawk header

       Extract client id and ext data
    '''
    if not header.startswith('Hawk '):
        raise Exception('Missing Hawk prefix')

    # Load header parts
    parts = re.findall(r'(\w+)="([\w=\.\@\-_/]+)"', header)
    if parts is None:
        raise Exception('Invalid header structure')
    parts = dict(parts)
    for k in ('id', 'mac', 'ts', 'nonce'):
        if k not in parts:
            raise Exception('Missing header part {}'.format(k))

    # TODO: check mac

    # Load ext data
    try:
        ext_data = json.loads(base64.b64decode(parts['ext']).decode('utf-8'))
    except Exception:
        ext_data = {}

    return parts['id'], ext_data


def mock_auth_taskcluster(request):
    '''Mock the hawk header validation from Taskcluster.
    '''
    payload = json.loads(request.body)
    try:
        # Parse fake hawk header
        if 'authorization' not in payload:
            raise Exception('Missing authorization')
        client_id, ext_data = parse_header(payload['authorization'])

        # Build success response
        expires = datetime.datetime.now() + datetime.timedelta(days=1)
        body = {
            'status': 'auth-success',
            'scopes': ext_data.get('scopes', []),
            'scheme': 'hawk',
            'clientId': client_id,
            'expires': expires.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        http_code = 200

    except Exception as e:
        # Build failure response
        body = {
            'status': 'auth-failure',
            'message': str(e),
        }
        http_code = 401

    # Output response
    headers = {
        'Content-Type': 'application/json'
    }
    return (http_code, headers, json.dumps(body))


def mock_auth_auth0(request):
    '''Mock the auth0 userinfo endpoint.

       Currently used to validate access tokens and get user info
    '''

    # request is a PreparedRequest not a Request object, so
    # have extract the query parameters.
    url = urllib.parse.urlparse(request.url)
    query = {param.split('=')[0]: param.split('=')[1]
             for param in url.query.split('&')}

    if query.get('access_token', 'badtoken') == 'badtoken':
        body = b'Unauthorized'
        headers = {
            'Content-Type': 'text/plain'
        }
    else:
        body = json.dumps(AUTH0_DUMMY_USERINFO)
        headers = {
            'Content-Type': 'application/json'
        }

    # auth0 always returns a 200, even if the token was invalid.
    http_code = 200
    return (http_code, headers, body)


def configure_app(app):
    '''Configure flask application and ensure all mocks are in place
    '''

    if hasattr(app, 'db'):
        app.db.drop_all()
        app.db.create_all()


@pytest.fixture(autouse=True)
def client(app):
    '''A Flask test client for uplift/backend with mockups enabled.
    '''
    with app.test_client() as client:
        with requests_mock:

            if hasattr(app, 'auth'):
                requests_mock.add_callback(
                    responses.POST,
                    'https://auth.taskcluster.net/v1/authenticate-hawk',
                    callback=mock_auth_taskcluster,
                    content_type='application/json',
                )

            if hasattr(app, 'auth0'):
                requests_mock.add_callback(
                    responses.GET,
                    re.compile(r'https://auth\.mozilla\.auth0\.com/userinfo.*'),
                    callback=mock_auth_auth0,
                )

            yield client


def app_heartbeat():
    pass
