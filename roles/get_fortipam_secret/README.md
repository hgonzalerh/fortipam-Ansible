get_fortipam_secret
=========

A role to fetch a secret from FortiPAM using only Ansible tasks (no custom plugins)

Requirements
------------

Role Variables
--------------

### Input
`get_fortipam_secret_fortipam_instance`: Also FORTIPAM_INSTANCE from environment, url of the appliance or SaaS from FortiPAM. Example: https://my-fortipam-instance:8080/

`get_fortipam_secret_fortipam_token`: Also FORTIPAM_TOKEN from environment, the token that was provisioned for an API user from a FortiPAM admin, for authentication. 

`get_fortipam_secret_secret_name`: The name of the secret - this matches the GUI name in FortiPAM. Also can be a numeric ID

`get_fortipam_secret_secret_field`: The name of the field within the secret - this matches the GUI name in FortiPAM.

`get_fortipam_secret_skip_tls_verify`: Whether to skip cert verification, true means accept an invalid cert, false means enforce a valid cert

### Output
`get_fortipam_secret_field_cleartext`: The contents of only the desired field in the secret

`get_fortipam_secret_full_array`: All the fields in the secret


Dependencies
------------


Example Playbook
----------------

Use in a play before your actual automation to managed hosts, to get passwords 
and auth for them

```yaml
- name: Fetch FortiPAM secret credentials
  hosts: localhost
  gather_facts: false
  vars:
    fpam_secret: "Hugo"
    fpam_field: "Password"

    fpam_instance: "{{ lookup('env', 'FORTIPAM_INSTANCE') }}"
    fpam_token: "{{ lookup('env', 'FORTIPAM_TOKEN') }}"
    fpam_skip_tls_verify: true

  tasks:
    - name: Get the FortiPAM secret and field - {{ fpam_secret }} - {{ fpam_field }}
      ansible.builtin.include_role:
        name: get_fortipam_secret
      vars:
        get_fortipam_secret_secret_name: "{{ fpam_secret }}"
        get_fortipam_secret_secret_field: "{{ fpam_field }}"
        get_fortipam_secret_skip_tls_verify: true

    - name: Print the secret object
      ansible.builtin.debug:
        msg: Object is {{ get_fortipam_secret_full_array }}

    - name: Print the extracted field
      ansible.builtin.debug:
        msg: 'Field cleartext is: {{ get_fortipam_secret_field_cleartext }}'
```


License
-------

MIT

Author Information
------------------

Hugo F. Gonzalez <hgonzale@redhat.com> 

