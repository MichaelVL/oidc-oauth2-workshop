#!/usr/bin/env python

import os
import flask
import requests
import urllib
import uuid
import base64
import json
import logging
import jose

from authlib.jose import jwt, jwk, JsonWebKey
from jose import jwt as jose_jwt

app = flask.Flask('oauth2-client')

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger('oauth2-client')

sessions = dict()

oauth2_url = os.getenv('OAUTH2_URL', 'http://localhost:5001/authorize')
oauth2_token_url = os.getenv('OAUTH2_TOKEN_URL', 'http://localhost:5001/token')
oauth2_userinfo_url = os.getenv('OAUTH2_USERINFO_URL', 'http://localhost:5001/userinfo')
oidc_jwks_url = os.getenv('OIDC_JWKS_URL', 'http://localhost:5001/.well-known/jwks.json')
client_id = os.getenv('CLIENT_ID', 'client-123-id')
client_secret = os.getenv('CLIENT_SECRET', 'client-123-password')
app_port = int(os.getenv('APP_PORT', '5000'))
api_base_url = os.getenv('API_BASE_URL', 'http://localhost:5002')

own_url = 'http://localhost:5000'
redirect_uri = 'http://localhost:5000/callback'

SESSION_COOKIE_NAME='client-session'

def build_url(url, **kwargs):
    return '{}?{}'.format(url, urllib.parse.urlencode(kwargs))

def encode_client_creds(client_id, client_secret):
    creds = '{}:{}'.format(urllib.parse.quote_plus(client_id), urllib.parse.quote_plus(client_secret))
    return base64.b64encode(creds.encode('ascii')).decode('ascii')

def json_pretty_print(json_data):
    return json.dumps(json_data, indent=4, sort_keys=True)

def token_get_jwk(token):
    response = requests.get(oidc_jwks_url)
    jwks = response.json()
    log.info("Got JWKS '{}'".format(jwks))
    hdr = jose_jwt.get_unverified_header(token)
    log.info("JWT header '{}'".format(hdr))
    for jwk in jwks['keys']:
        if 'kid' in jwk.keys() and jwk['kid'] == hdr['kid']:
            return jwk
    return None

@app.route('/', methods=['GET'])
def index():
    req = flask.request
    session_cookie = req.cookies.get(SESSION_COOKIE_NAME)
    if session_cookie in sessions:
        session = sessions[session_cookie]
        return flask.render_template('token.html',
                                     id_token=session['id_token'],
                                     id_token_parsed=json_pretty_print(session['id_token_claims']),
                                     username=session['id_token_claims']['sub'],
                                     access_token=session['access_token'],
                                     refresh_token=session['refresh_token'])
    else:
        return flask.render_template('index.html', client_id=client_id, oauth2_url=oauth2_url)

@app.route('/gettoken', methods=['POST'])
def gettoken():
    req = flask.request
    scope = req.form.get('scope')
    response_type = 'code'
    state = str(uuid.uuid4())
    redir_url = build_url(oauth2_url, response_type=response_type, client_id=client_id, scope=scope, redirect_uri=redirect_uri, state=state)
    log.info("Redirecting get-token to '{}'".format(redir_url))
    return flask.redirect(redir_url, code=303)

@app.route('/callback', methods=['GET'])
def callback():
    req = flask.request

    code = req.args.get('code')
    state = req.args.get('state')

    # TODO: Check state is valid for an outstanding request

    log.info("Got callback with code '{}'".format(code))
    if not code:
        log.error('Received no code: {}'.format(req))

    data = {'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': redirect_uri}
    headers = {'Authorization': 'Basic '+encode_client_creds(client_id, client_secret),
               'Content-type': 'application/x-www-form-urlencoded'}

    log.info("Getting token from url: '{}'".format(oauth2_token_url))
    response = requests.post(oauth2_token_url, data=data, headers=headers)

    if response.status_code != 200:
        return 'Failed with status {}: {}'.format(response.status_code, response.text)

    response_json = response.json()
    for token_type in ['id_token', 'access_token', 'refresh_token']:
        if token_type in response_json:
            log.info("Got {} token '{}'".format(token_type, response_json[token_type]))

    id_token = response_json['id_token']
    access_token=response_json['access_token']
    refresh_token=response_json['refresh_token']

    token_pub_jwk_json = token_get_jwk(id_token)
    token_pub_jwk = JsonWebKey.import_key(token_pub_jwk_json)

    claims = jwt.decode(id_token, token_pub_jwk)

    session_id = str(uuid.uuid4())
    session = {'id_token': id_token,
               'id_token_claims': claims,
               'access_token': access_token,
               'refresh_token' : refresh_token}
    sessions[session_id] = session
    log.info('Created session {}'.format(session_id))

    resp = flask.make_response(flask.redirect(own_url, code=303))
    resp.set_cookie(SESSION_COOKIE_NAME, session_id, samesite='Lax', httponly=True)
    return resp

@app.route('/getuserinfo', methods=['POST'])
def get_userinfo():
    req = flask.request
    access_token = req.form.get('accesstoken')
    log.info('Get UserInfo, access-token: {}'.format(access_token))

    # FIXME bearer, type
    headers = {'Authorization': 'Bearer '+access_token}

    log.info("Getting userinfo from url: '{}'".format(oauth2_userinfo_url))
    response = requests.get(oauth2_userinfo_url, headers=headers)

    if response.status_code != 200:
        return 'Failed with status {}'.format(response.status_code)

    response_json = response.json()
    return flask.render_template('userinfo.html', access_token=access_token,
                                 userinfo=json_pretty_print(response_json))

@app.route('/read-api', methods=['POST'])
def read_api():
    req = flask.request
    access_token = req.form.get('accesstoken')
    log.info('Read API, access-token: {}'.format(access_token))

    # FIXME bearer, type
    auth_token_usage = req.form.get('auth-token-usage')
    log.info('Read API, token usage: {}'.format(auth_token_usage))
    if auth_token_usage == 'authentication-header':
        headers = {'Authorization': 'Bearer '+access_token}
    else:
        headers = {}

    log.info("Reading from API url: '{}'".format(api_base_url))
    response = requests.get(api_base_url+'/api', headers=headers)

    if response.status_code != 200:
        return 'Failed with code {}, headers: {}'.format(response.status_code, response.headers)

    response_json = response.json()
    return flask.render_template('read-api.html', access_token=access_token,
                                 api_data=json_pretty_print(response_json))

@app.route('/refresh-token', methods=['POST'])
def refresh_token():
    req = flask.request
    session_cookie = req.cookies.get(SESSION_COOKIE_NAME)
    refresh_token = req.form.get('refreshtoken')
    log.info('Refresh token, refresh-token: {}'.format(refresh_token))

    data = {'refresh_token': refresh_token,
            'grant_type': 'refresh_token'}
    headers = {'Authorization': 'Basic '+encode_client_creds(client_id, client_secret),
               'Content-type': 'application/x-www-form-urlencoded'}

    log.info("Refresh token from url: '{}'".format(oauth2_token_url))
    response = requests.post(oauth2_token_url, data=data, headers=headers)

    if response.status_code != 200:
        return 'Failed with status {}: {}'.format(response.status_code, response.text)

    response_json = response.json()
    for token_type in ['id_token', 'access_token', 'refresh_token']:
        if token_type in response_json:
            log.info("Got {} token '{}'".format(token_type, response_json[token_type]))

    id_token = response_json['id_token']
    access_token = response_json['access_token']

    token_pub_jwk_json = token_get_jwk(id_token)
    token_pub_jwk = JsonWebKey.import_key(token_pub_jwk_json)

    claims = jwt.decode(id_token, token_pub_jwk)

    session = {'id_token': id_token,
               'id_token_claims': claims,
               'access_token': access_token}
    if 'refresh_token' in response_json:
        session['refresh_token'] = response_json['refresh_token']
    sessions[session_cookie] = session

    resp = flask.make_response(flask.redirect(own_url, code=303))
    return resp

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=app_port)