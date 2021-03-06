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

import eventlet
from novaclient import exceptions as nova_exceptions
from oslo_config import cfg
from oslo_log import log as logging

from climate import exceptions as climate_exceptions
from climate.plugins import base
from climate.plugins import instances as plugin
from climate.utils.openstack import nova

LOG = logging.getLogger(__name__)

plugin_opts = [
    cfg.StrOpt('on_end',
               default='create_image, delete',
               help='Actions which we will use in the end of the lease'),
    cfg.StrOpt('on_start',
               default='on_start',
               help='Actions which we will use at the start of the lease'),
]

CONF = cfg.CONF
CONF.register_opts(plugin_opts, group=plugin.RESOURCE_TYPE)


class VMPlugin(base.BasePlugin, nova.NovaClientWrapper):
    """Base plugin for VM reservation."""
    resource_type = plugin.RESOURCE_TYPE
    title = "Basic VM Plugin"
    description = ("This is basic plugin for VM management. "
                   "It can start, snapshot and suspend VMs")

    def on_start(self, resource_id):
        try:
            self.nova.servers.unshelve(resource_id)
        except nova_exceptions.Conflict:
            LOG.error("Instance have been unshelved")

    def on_end(self, resource_id):
        actions = self._split_actions(CONF[plugin.RESOURCE_TYPE].on_end)

        # actions will be processed in following order:
        # - create image from VM
        # - suspend VM
        # - delete VM
        # this order guarantees there will be no situations like
        # creating snapshot or suspending already deleted instance

        if 'create_image' in actions:
            with eventlet.timeout.Timeout(600, climate_exceptions.Timeout):
                try:
                    self.nova.servers.create_image(resource_id)
                    eventlet.sleep(5)
                    while not self._check_active(resource_id):
                        eventlet.sleep(1)
                except nova_exceptions.NotFound:
                    LOG.error('Instance %s has been already deleted. '
                              'Cannot create image.' % resource_id)
                except climate_exceptions.Timeout:
                    LOG.error('Image create failed with timeout. Take a look '
                              'at nova.')
                except nova_exceptions.Conflict as e:
                    LOG.warning('Instance is in a invalid state for'
                                'create_image. Take a look at nova.'
                                '(Request-ID: %s)' % e.request_id)

        if 'suspend' in actions:
            try:
                self.nova.servers.suspend(resource_id)
            except nova_exceptions.NotFound:
                LOG.error('Instance %s has been already deleted. '
                          'Cannot suspend instance.' % resource_id)

        if 'delete' in actions:
            try:
                self.nova.servers.delete(resource_id)
            except nova_exceptions.NotFound:
                LOG.error('Instance %s has been already deleted. '
                          'Cannot delete instance.' % resource_id)

    def _check_active(self, resource_id):
        instance = self.nova.servers.get(resource_id)
        task_state = getattr(instance, 'OS-EXT-STS:task_state', None)
        if task_state is None:
            return True

        if task_state.upper() in ['IMAGE_SNAPSHOT', 'IMAGE_PENDING_UPLOAD',
                                  'IMAGE_UPLOADING']:
            return False
        else:
            LOG.error('Nova reported unexpected task status %s for '
                      'instance %s' % (task_state, resource_id))
            raise climate_exceptions.TaskFailed()

    def _split_actions(self, actions):
        try:
            return actions.replace(' ', '').split(',')
        except AttributeError:
            raise climate_exceptions.WrongFormat()
