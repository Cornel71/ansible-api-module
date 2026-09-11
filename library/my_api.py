#!/usr/bin/python
# -*- coding: utf-8 -*-

from ansible.module_utils.basic import AnsibleModule
import urllib.request
import urllib.error
import json
import ssl
import time

DOCUMENTATION = r'''
---
module: my_api
short_description: Generic REST API module optimized for AWX / Automation Controller structures.
description:
  - Manages REST API requests (GET, POST, PATCH, DELETE) using implicit JSON formatting.
  - Supports Bearer Token or Basic Authentication (Username/Password).
  - Automatically handles AWX-specific pagination using the 'results' and 'next' keys.
  - Includes Auto-Retry logic for transient server errors (HTTP 429 and 503).
  - Allows recursive structural mapping of complex JSON trees via the 'dump_structure' option.
options:
  base_url:
    description: The base URL of the API or platform (e.g., https://----).
    required: true
    type: str
  api_version:
    description: The API version prefix for the URL. Leave empty if using absolute paths from endpoints.
    default: api/v2
    type: str
  endpoint:
    description: The specific endpoint path (e.g., /users/1/ or dynamically extracted from related fields).
    required: true
    type: str
  method:
    description: The HTTP method to use for the request.
    default: GET
    choices: [GET, POST, PATCH, DELETE]
    type: str
  token:
    description: The security Bearer Token for authentication.
    type: str
  username:
    description: The username for Basic Authentication.
    type: str
  password:
    description: The password for Basic Authentication.
    type: str
    no_log: true
  data:
    description: A Python dictionary (payload) that will be automatically converted to JSON for POST/PATCH.
    type: dict
  dump_structure:
    description: If true, returns only the data-type schema tree of the JSON keys for debugging.
    type: bool
    default: false
  auto_pagination:
    description: Automatically follows 'next' page links and aggregates lists into a single result.
    type: bool
    default: false
  max_retries:
    description: Number of retry attempts in case of HTTP 429 or 503 errors.
    type: int
    default: 3
  retry_delay:
    description: Waiting time in seconds between retries unless specified otherwise by the server header.
    type: int
    default: 5
  validate_certs:
    description: Allows bypassing SSL certificate verification (useful for self-signed environments).
    type: bool
    default: true
author:
  - Cornel-Mihai Badea
  - cornel@companyx.ro
'''

EXAMPLES = r'''
# Fetching user details
- name: Get user details
  my_api:
    base_url: "{{ url }}"
    endpoint: "/users/1/"
    token: "my_token_here"
    validate_certs: false

# Updating specific fields partially (PATCH)
- name: Update email address
  my_api:
    base_url: "{{ url }}"
    endpoint: "/users/1/"
    method: PATCH
    token: "my_token_here"
    data:
      email: "new@email.com"
'''

RETURN = r'''
json:
  description: The raw response returned by the API, automatically parsed from JSON into a dict or list.
  returned: success
  type: complex
status:
  description: The HTTP status code returned by the server (e.g., 200, 201).
  returned: always
  type: int
api_structure:
  description: The structural schema of the JSON, returned only when dump_structure=true.
  returned: optional
  type: dict
'''

def dump_recursive_structure(data):
    if isinstance(data, dict):
        return {k: dump_recursive_structure(v) if isinstance(v, dict) else ([dump_recursive_structure(v)] if isinstance(v, list) and len(v) > 0 and isinstance(v, dict) else (f"list_of_{type(v).__name__}" if isinstance(v, list) and len(v) > 0 else ("empty_list" if isinstance(v, list) else type(v).__name__))) for k, v in data.items()}
    return type(data).__name__

def make_http_request(url, method, data, headers, context, module, max_retries, retry_delay):
    req = urllib.request.Request(url, data=None if method == 'GET' else data, headers=headers, method=method)
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, context=context, timeout=30) as response:
                body = response.read().decode('utf-8')
                return response.status, json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            if e.code in [429, 503] and attempt < max_retries:
                time.sleep(int(e.headers.get('Retry-After', retry_delay)))
                continue
            err_b = e.read().decode('utf-8', errors='ignore')
            try: err_j = json.loads(err_b)
            except: err_j = err_b
            module.fail_json(msg=f"HTTP Error {e.code}: {e.reason}", error_details=err_j, url=url)
        except Exception as e:
            if attempt < max_retries:
                time.sleep(retry_delay)
                continue
            module.fail_json(msg=f"Network error: {str(e)}", url=url)

def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            base_url=dict(type='str', required=True),
            api_version=dict(type='str', default='api/v2'),
            endpoint=dict(type='str', required=True),
            method=dict(type='str', default='GET', choices=['GET', 'POST', 'PATCH', 'DELETE']),
            token=dict(type='str'), username=dict(type='str'), password=dict(type='str', no_log=True),
            data=dict(type='dict'), dump_structure=dict(type='bool', default=False),
            auto_pagination=dict(type='bool', default=False), max_retries=dict(type='int', default=3),
            retry_delay=dict(type='int', default=5), validate_certs=dict(type='bool', default=True)
        ),
        supports_check_mode=True
    )
    
    p = module.params
    curr_url = f"{p['base_url'].rstrip('/')}/{p['api_version'].strip('/')}/{p['endpoint'].lstrip('/')}" if p['api_version'] else f"{p['base_url'].rstrip('/')}/{p['endpoint'].lstrip('/')}"
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    
    if p['token']: headers['Authorization'] = f"Bearer {p['token']}"
    elif p['username'] and p['password']:
        import base64
        credentials = f"{p['username']}:{p['password']}".encode('utf-8')
        b64_auth = base64.b64encode(credentials).decode('utf-8')
        headers['Authorization'] = f"Basic {b64_auth}"
        
    req_data = json.dumps(p['data']).encode('utf-8') if p['data'] and p['method'] in ['POST', 'PATCH'] else None
    ctx = ssl.create_default_context() if p['validate_certs'] else ssl._create_unverified_context()

    if p['method'] in ['PATCH', 'DELETE']:
        try:
            g_req = urllib.request.Request(curr_url, headers=headers, method='GET')
            with urllib.request.urlopen(g_req, context=ctx, timeout=15) as g_res:
                curr_state = json.loads(g_res.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                if p['method'] == 'DELETE': module.exit_json(changed=False, msg="Resource already deleted (404).")
                else: module.fail_json(msg="Target resource does not exist (404).")
            module.fail_json(msg=f"Pre-check failed: HTTP {e.code}", url=curr_url)

        if p['method'] == 'PATCH' and p['data']:
            if all(curr_state.get(k) == v for k, v in p['data'].items()):
                module.exit_json(changed=False, json=curr_state, msg="No modifications required.")

        if module.check_mode:
            if p['method'] == 'DELETE': module.exit_json(changed=True, msg="[Check Mode] Resource would be deleted.")
            sim = curr_state.copy(); sim.update(p['data'] or {})
            module.exit_json(changed=True, json=sim, msg="[Check Mode] Updates would be applied.")
            
    elif p['method'] == 'POST' and module.check_mode:
        module.exit_json(changed=True, msg="[Check Mode] Resource would be created.")

    agg, is_pag, has_more, status, meta = [], False, True, 200, {}
    while has_more:
        status, json_out = make_http_request(curr_url, p['method'], req_data, headers, ctx, module, p['max_retries'], p['retry_delay'])
        if p['method'] == 'GET' and isinstance(json_out, dict) and 'results' in json_out and 'next' in json_out:
            is_pag = True
            agg.extend(json_out['results'])
            if not meta: meta = {k: v for k, v in json_out.items() if k != 'results'}
            if p['auto_pagination'] and json_out.get('next'):
                nxt = json_out['next']
                curr_url = nxt if nxt.startswith('http') else f"{p['base_url'].rstrip('/')}{nxt}"
            else: has_more = False
        else:
            agg = json_out
            has_more = False

    if p['dump_structure']:
        tgt = agg if is_pag and len(agg) > 0 else agg
        module.exit_json(changed=False, api_structure=dump_recursive_structure(tgt), is_list=is_pag, meta=meta)

    module.exit_json(changed=(p['method'] in ['POST', 'PATCH', 'DELETE']), json=agg, meta=meta if is_pag else None, status=status)

if __name__ == '__main__':
    run_module()
