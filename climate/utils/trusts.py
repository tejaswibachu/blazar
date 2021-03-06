# Copyright (c) 2013 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import functools

from oslo_config import cfg

from climate import context
from climate.utils.openstack import keystone

CONF = cfg.CONF


def create_trust():
    """Creates trust via Keystone API v3 to use in plugins."""
    client = keystone.ClimateKeystoneClient()

    trustee_id = keystone.ClimateKeystoneClient(
        username=CONF.os_admin_username,
        password=CONF.os_admin_password,
        tenant_name=CONF.os_admin_project_name).user_id

    ctx = context.current()
    trust = client.trusts.create(trustor_user=ctx.user_id,
                                 trustee_user=trustee_id,
                                 impersonation=False,
                                 role_names=ctx.roles,
                                 project=ctx.project_id)
    return trust


def delete_trust(lease):
    """Deletes trust for the specified lease."""
    if lease.trust_id:
        client = keystone.ClimateKeystoneClient(trust_id=lease.trust_id)
        client.trusts.delete(lease.trust_id)


def create_ctx_from_trust(trust_id):
    """Return context built from given trust."""
    ctx = context.ClimateContext(
        user_name=CONF.os_admin_username,
        project_name=CONF.os_admin_project_name,
    )
    auth_url = "%s://%s:%s/v3" % (CONF.os_auth_protocol,
                                  CONF.os_auth_host,
                                  CONF.os_auth_port)
    client = keystone.ClimateKeystoneClient(
        password=CONF.os_admin_password,
        trust_id=trust_id,
        auth_url=auth_url,
        ctx=ctx,
    )

    # use 'with ctx' statement in the place you need context from trust
    return context.ClimateContext(
        ctx,
        auth_token=client.auth_token,
        service_catalog=client.service_catalog.catalog['catalog'],
        project_id=client.tenant_id,
    )


def use_trust_auth():
    """Decorator creates a keystone trust

    This decorator creates a keystone trust, and adds the trust_id to the
    parameter of the decorated method.
    """
    def decorator(func):

        @functools.wraps(func)
        def wrapped(self, to_update):
            if to_update is not None:
                trust = create_trust()
                if isinstance(to_update, dict):
                    to_update.update({'trust_id': trust.id})
                elif isinstance(to_update, object):
                    setattr(to_update, 'trust_id', trust.id)
            return func(self, to_update)

        return wrapped
    return decorator
