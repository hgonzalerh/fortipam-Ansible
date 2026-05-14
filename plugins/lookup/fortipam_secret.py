# (c) 2026, Hugo
# GNU General Public License v3.0+
from __future__ import annotations

DOCUMENTATION = r"""
name: fortipam_secret
author: Hugo
short_description: Retrieve a secret field from FortiPAM
description:
  - 'Retrieves a single named field from a FortiPAM secret.'
  - 'Each lookup term may be either a numeric secret ID or a secret name.
    Numeric terms are fetched directly; non-numeric terms are resolved via
    a server-side filter on the secret name, then fetched by the resolved ID.'
  - 'Secrets in FortiPAM store their content as a list of named fields where
    each entry has a name (as shown in the GUI, e.g. Password, Username, Host)
    and a value. This plugin returns the value of the entry whose name matches
    the lookup field option (case-insensitive).'
  - 'Authenticates with an API user API key sent as a bearer token.'
options:
  _terms:
    description: 'One or more FortiPAM secret IDs or names to look up.'
    required: true
  field:
    description: 'Name of the field to return, matched case-insensitively.'
    type: str
    default: Password
  by:
    description: 'How to interpret each term. auto treats numeric terms as IDs
      and everything else as names. id forces ID. name forces name.'
    type: str
    choices: [auto, id, name]
    default: auto
  validate_certs:
    description: 'Whether to verify the FortiPAM TLS certificate.'
    type: bool
    default: true
  timeout:
    description: 'HTTP timeout in seconds.'
    type: int
    default: 10
notes:
  - 'Requires FORTIPAM_INSTANCE and FORTIPAM_TOKEN in the environment.'
  - 'Name resolution requires the secret name to be unique. Multiple matches
    cause a hard failure rather than picking one.'
  - 'Name-to-ID resolutions are cached for the duration of the Ansible run.'
  - 'Always pair with no_log on the consuming task.'
"""

EXAMPLES = """
- name: Look up by name (recommended)
  ansible.builtin.debug:
    msg: "{{ lookup('hugonz.fortipam.secret', 'Hugo') }}"
  no_log: true

- name: Look up by numeric ID
  ansible.builtin.debug:
    msg: "{{ lookup('hugonz.fortipam.secret', '8') }}"
  no_log: true

- name: Fetch multiple fields from the same secret
  ansible.builtin.set_fact:
    db_host: "{{ lookup('hugonz.fortipam.secret', 'Hugo', field='Host') }}"
    db_user: "{{ lookup('hugonz.fortipam.secret', 'Hugo', field='Username') }}"
    db_pass: "{{ lookup('hugonz.fortipam.secret', 'Hugo') }}"
  no_log: true

- name: Force name interpretation for a secret named with digits
  ansible.builtin.debug:
    msg: "{{ lookup('hugonz.fortipam.secret', '2024', by='name') }}"
  no_log: true
"""

RETURN = """
  _raw:
    description: The value of the requested field, one entry per term.
    type: list
    elements: str
"""

import json
import os
from urllib.parse import quote, urlencode

from ansible.errors import AnsibleError, AnsibleLookupError
from ansible.module_utils.urls import Request
from ansible.plugins.lookup import LookupBase
from ansible.utils.display import Display

display = Display()

# Per-process cache: (instance, name) -> resolved numeric ID.
_NAME_CACHE: dict = {}


class LookupModule(LookupBase):

    def run(self, terms, variables=None, **kwargs):
        self.set_options(var_options=variables, direct=kwargs)

        instance = os.environ.get("FORTIPAM_INSTANCE")
        token = os.environ.get("FORTIPAM_TOKEN")
        if not instance:
            raise AnsibleError("FORTIPAM_INSTANCE environment variable is not set")
        if not token:
            raise AnsibleError("FORTIPAM_TOKEN environment variable is not set")

        instance = instance.rstrip("/")
        field = self.get_option("field")
        by = self.get_option("by")
        validate_certs = self.get_option("validate_certs")
        timeout = self.get_option("timeout")

        req = Request(
            headers={
                "Authorization": "Bearer {0}".format(token),
                "Accept": "application/json",
            },
            validate_certs=validate_certs,
            timeout=timeout,
        )

        results = []
        for term in terms:
            if term is None or str(term) == "":
                raise AnsibleLookupError("Empty term passed to fortipam.secret")
            term_s = str(term)
            secret_id = self._resolve_id(req, instance, term_s, by)
            results.append(
                self._fetch_field(req, instance, secret_id, field, term_s)
            )
        return results

    # -- term resolution ---------------------------------------------------

    def _resolve_id(self, req, instance, term, by):
        if by == "id":
            return term
        if by == "name":
            return self._lookup_id_by_name(req, instance, term)
        # auto: numeric → ID, anything else → name
        if term.isdigit():
            return term
        return self._lookup_id_by_name(req, instance, term)

    @staticmethod
    def _lookup_id_by_name(req, instance, name):
        cache_key = (instance, name)
        cached = _NAME_CACHE.get(cache_key)
        if cached is not None:
            display.vvv(
                "fortipam.secret: name '{0}' resolved from cache to id {1}".format(
                    name, cached
                )
            )
            return cached

        query = urlencode({"filter": "name=={0}".format(name)})
        url = "{0}/api/v2/cmdb/secret/database?{1}".format(instance, query)
        display.vvv("fortipam.secret: GET {0}".format(url))

        try:
            resp = req.get(url)
            body = resp.read()
        except Exception as e:
            raise AnsibleLookupError(
                "FortiPAM name-resolution request failed for '{0}': {1}".format(
                    name, e
                )
            )

        try:
            payload = json.loads(body)
        except ValueError as e:
            raise AnsibleLookupError(
                "FortiPAM name-resolution returned non-JSON for '{0}': {1}".format(
                    name, e
                )
            )

        status = payload.get("status") if isinstance(payload, dict) else None
        if status and status != "OK":
            cli_error = payload.get("cli_error", "") or payload.get("error", "")
            raise AnsibleLookupError(
                "FortiPAM returned {0} while resolving name '{1}': {2}".format(
                    status, name, cli_error
                )
            )

        matches = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(matches, list) or not matches:
            raise AnsibleLookupError(
                "No FortiPAM secret found with name '{0}'".format(name)
            )
        if len(matches) > 1:
            ids = [
                str(m.get("id")) for m in matches
                if isinstance(m, dict) and m.get("id") is not None
            ]
            raise AnsibleLookupError(
                "Multiple FortiPAM secrets named '{0}' (ids: {1}); "
                "look up by numeric id instead".format(name, ", ".join(ids))
            )

        secret_id = matches[0].get("id")
        if secret_id is None:
            raise AnsibleLookupError(
                "FortiPAM match for '{0}' has no 'id' field".format(name)
            )

        secret_id_s = str(secret_id)
        _NAME_CACHE[cache_key] = secret_id_s
        return secret_id_s

    # -- field fetch -------------------------------------------------------

    def _fetch_field(self, req, instance, secret_id, field_name, term_for_error):
        url = "{0}/api/v2/cmdb/secret/database/{1}".format(
            instance, quote(secret_id, safe="")
        )
        display.vvv("fortipam.secret: GET {0}".format(url))

        try:
            resp = req.get(url)
            body = resp.read()
        except Exception as e:
            raise AnsibleLookupError(
                "FortiPAM request failed for secret '{0}' (id {1}): {2}".format(
                    term_for_error, secret_id, e
                )
            )

        try:
            payload = json.loads(body)
        except ValueError as e:
            raise AnsibleLookupError(
                "FortiPAM returned non-JSON for secret '{0}' (id {1}): {2}".format(
                    term_for_error, secret_id, e
                )
            )

        status = payload.get("status") if isinstance(payload, dict) else None
        http_status = payload.get("http_status") if isinstance(payload, dict) else None
        if status and status != "OK":
            cli_error = payload.get("cli_error", "") or payload.get("error", "")
            raise AnsibleLookupError(
                "FortiPAM returned {0} ({1}) for secret '{2}' (id {3}): {4}".format(
                    status, http_status, term_for_error, secret_id, cli_error
                )
            )

        return self._extract_field(payload, field_name, term_for_error, secret_id)

    @staticmethod
    def _extract_field(payload, field_name, term_for_error, secret_id):
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            raise AnsibleLookupError(
                "FortiPAM returned no results for secret '{0}' (id {1})".format(
                    term_for_error, secret_id
                )
            )

        record = results[0]
        fields = record.get("field") if isinstance(record, dict) else None
        if not isinstance(fields, list):
            raise AnsibleLookupError(
                "Secret '{0}' (id {1}) has no 'field' array in response".format(
                    term_for_error, secret_id
                )
            )

        wanted = field_name.casefold()
        for entry in fields:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if isinstance(name, str) and name.casefold() == wanted:
                return entry.get("value")

        available = [
            e.get("name") for e in fields
            if isinstance(e, dict) and isinstance(e.get("name"), str)
        ]
        raise AnsibleLookupError(
            "Field '{0}' not found in secret '{1}' (id {2}). "
            "Available fields: {3}".format(
                field_name, term_for_error, secret_id,
                ", ".join(available) or "(none)",
            )
        )
