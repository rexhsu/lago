#
# Copyright 2014 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
"""
VM Plugins
============
There are two VM-related plugin extension points, there's the
`VM Type Plugin`_, that allows you to modify at a higher level the inner
workings of the VM class (domain concept in the initfile).
The other plugin extension point, the [VM Provider Plugin], that allows you to
create an alternative implementation of the provisioning details for the VM,
for example, using a remote libvirt instance or similar.
"""
import contextlib
import functools
import logging
import os
import warnings
from abc import (ABCMeta, abstractmethod)

from scp import SCPClient

from .. import (utils, log_utils, plugins, ssh, )
from lago.config import config

LOGGER = logging.getLogger(__name__)
LogTask = functools.partial(log_utils.LogTask, logger=LOGGER)


class VMErrror(Exception):
    pass


class ExtractPathError(VMErrror):
    pass


def _check_alive(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.alive():
            raise RuntimeError('VM %s is not running' % self.name())
        return func(self, *args, **kwargs)

    return wrapper


class VMProviderPlugin(plugins.Plugin):
    """
    If you want to use a custom provider for you VMs (say, ovirt for example),
    you have to inherit from this class, and then define the
    'default_vm_provider' in your config to be your plugin, or explicitly
    specify it on each domain definition in the initfile with 'vm-provider' key

    You will have to override at least all the abstractmethods in order to
    write a provider plugin, even if they are just runnig `pass`.
    """

    def __init__(self, vm):
        self.vm = vm

    @abstractmethod
    def start(self, *args, **kwargs):
        """
        Start a domain

        Returns:
            None
        """
        pass

    @abstractmethod
    def stop(self, *args, **kwargs):
        """
        Stop a domain

        Returns:
            None
        """
        pass

    @abstractmethod
    def defined(self, *args, **kwargs):
        """
        Return if the domain is defined (libvirt concept), currently used only
        by the libvirt provider, put here to allow backwards compatibility.

        Returns:
            bool: True if the domain is already defined (libvirt concept)
        """
        pass

    @abstractmethod
    def bootstrap(self, *args, **kwargs):
        """
        Does any actions needed to get the domain ready to be used, ran on
        prefix init.

        Return:
            None
        """
        pass

    @abstractmethod
    def state(self, *args, **kwargs):
        """
        Return the current state of the domain

        Returns:
            str: Small description of the current domain state
        """
        pass

    @abstractmethod
    def create_snapshot(self, name, *args, **kwargs):
        """
        Take any actions needed to create a snapshot

        Args:
            name(str): Name for the snapshot, will be used as key to retrieve
                it later

        Returns:
            None
        """
        pass

    @abstractmethod
    def revert_snapshot(self, name, *args, **kwargs):
        """
        Take any actions needed to revert/restore a snapshot

        Args:
            name(str): Name for the snapshot, same that was set on creation

        Returns:
            None
        """
        pass

    @abstractmethod
    def vnc_port(self, *args, **kwargs):
        """
        Retrieve the vnc port that was configured for the domain

        Returns:
            str: string representing the vnc port number (or a helpful message,
                like 'no-vnc')
        """
        pass

    def interactive_console(self):
        """
        Run an interactive console

        Returns:
            lago.utils.CommandStatus: resulf of the interactive execution
        """
        return self.vm.interactive_ssh()

    def extract_paths(self, paths):
        """
        Extract the given paths from the domain

        Args:
            paths(list of str): paths to extract
        """
        if self.vm.alive() and self.vm.ssh_reachable():
            self._extract_paths_scp(paths=paths)
        elif self.vm.alive():
            raise ExtractPathError(
                'Unable to extract logs from alive but unreachable host %s. '
                'Try stopping it first' % self.vm.name()
            )
        else:
            raise ExtractPathError(
                'Unable to extract logs from alive but unreachable host %s. '
                'Try stopping it first' % self.vm.name()
            )

    def _extract_paths_scp(self, paths):
        for host_path, guest_path in paths:
            LOGGER.debug(
                'Extracting scp://%s:%s to %s',
                self.vm.name(),
                host_path,
                guest_path,
            )
            self.vm.copy_from(local_path=guest_path, remote_path=host_path)


class VMPlugin(plugins.Plugin):
    __metaclass__ = ABCMeta
    '''
    This class takes care of the high level abstraction for a VM (a domain in
    the initfile lingo). From starting/stopping it to loading and calling the
    provider if needed. If you want to change only the way the VM is
    provisioned you can take a look to the `class:VMProviderPlugin` instead.

    This base class includes also some basic methods implemented with ssh.

    VM properties:
    * name
    * cpus
    * memory
    * disks
    * metadata
    * network/mac addr
    * virt_env
    '''

    def __init__(self, env, spec):
        self.virt_env = env
        self._spec = self._normalize_spec(spec.copy())

        self._ssh_client = None
        self.service_providers = plugins.load_plugins(
            namespace=plugins.PLUGIN_ENTRY_POINTS['vm-service'],
            instantiate=False,
        )
        self._service_class = self._get_service_provider()
        self.vm_providers = plugins.load_plugins(
            namespace=plugins.PLUGIN_ENTRY_POINTS['vm-provider'],
            instantiate=False,
        )
        self.provider = self._get_vm_provider()

    def start(self, *args, **kwargs):
        """
        Thin method that just uses the provider
        """
        return self.provider.start(*args, **kwargs)

    def stop(self, *args, **kwargs):
        """
        Thin method that just uses the provider
        """
        return self.provider.stop(*args, **kwargs)

    def defined(self, *args, **kwargs):
        """
        Thin method that just uses the provider
        """
        return self.provider.defined(*args, **kwargs)

    def bootstrap(self, *args, **kwargs):
        """
        Thin method that just uses the provider
        """
        return self.provider.bootstrap(*args, **kwargs)

    def state(self, *args, **kwargs):
        """
        Thin method that just uses the provider
        """
        return self.provider.state(*args, **kwargs)

    def create_snapshot(self, name, *args, **kwargs):
        """
        Thin method that just uses the provider
        """
        return self.provider.create_snapshot(name, *args, **kwargs)

    def revert_snapshot(self, name, *args, **kwargs):
        """
        Thin method that just uses the provider
        """
        return self.provider.revert_snapshot(name, *args, **kwargs)

    def interactive_console(self, *args, **kwargs):
        """
        Thin method that just uses the provider
        """
        return self.provider.interactive_console(*args, **kwargs)

    def vnc_port(self, *args, **kwargs):
        """
        Thin method that just uses the provider
        """
        return self.provider.vnc_port(*args, **kwargs)

    def extract_paths(self, paths, *args, **kwargs):
        """
        Thin method that just uses the provider
        """
        return self.provider.extract_paths(paths, *args, **kwargs)

    def copy_to(self, local_path, remote_path, recursive=True):
        with LogTask(
            'Copy %s to %s:%s' % (local_path, self.name(), remote_path),
        ):
            with self._scp() as scp:
                scp.put(
                    files=local_path,
                    remote_path=remote_path,
                    recursive=recursive,
                )

    def copy_from(self, remote_path, local_path, recursive=True):
        with self._scp() as scp:
            scp.get(
                recursive=recursive,
                remote_path=remote_path,
                local_path=local_path,
            )

    @property
    def metadata(self):
        return self._spec['metadata'].copy()

    def name(self):
        return str(self._spec['name'])

    def iscsi_name(self):
        return 'iqn.2014-07.org.lago:%s' % self.name()

    def ip(self):
        return str(self.virt_env.get_net().resolve(self.name()))

    def ssh(
        self,
        command,
        data=None,
        show_output=True,
        propagate_fail=True,
        tries=None,
    ):
        if not self.alive():
            raise RuntimeError('Attempt to ssh into a not running host')

        return ssh.ssh(
            ip_addr=self.ip(),
            host_name=self.name(),
            command=command,
            data=None,
            show_output=True,
            propagate_fail=True,
            tries=None,
            ssh_key=self.virt_env.prefix.paths.ssh_id_rsa(),
            username=self._spec.get('ssh-user'),
            password=self._spec.get('ssh-password'),
        )

    def wait_for_ssh(self):
        return ssh.wait_for_ssh(
            ip_addr=self.ip(),
            host_name=self.name(),
            connect_timeout=self._spec.get('boot_time_sec', 600),
            ssh_key=self.virt_env.prefix.paths.ssh_id_rsa(),
            username=self._spec.get('ssh-user'),
            password=self._spec.get('ssh-password'),
        )

    def ssh_script(self, path, show_output=True):
        return ssh.ssh_script(
            ip_addr=self.ip(),
            host_name=self.name(),
            ssh_key=self.virt_env.prefix.paths.ssh_id_rsa(),
            path=path,
            show_output=show_output,
            username=self._spec.get('ssh-user'),
            password=self._spec.get('ssh-password'),
        )

    def alive(self):
        return self.state() == 'running'

    def ssh_reachable(self):
        try:
            ssh.get_ssh_client(
                ip_addr=self.ip(),
                host_name=self.name(),
                ssh_key=self.virt_env.prefix.paths.ssh_id_rsa(),
                username=self._spec.get('ssh-user'),
                password=self._spec.get('ssh-password'),
            )
        except RuntimeError:
            return False

        return True

    def save(self, path=None):
        if path is None:
            path = self.virt_env.virt_path('vm-%s' % self.name())

        dst_dir = os.path.dirname(path)
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)

        with open(path, 'w') as f:
            utils.json_dump(self._spec, f)

    @_check_alive
    def service(self, name):
        if self._service_class is None:
            self._detect_service_provider()

        return self._service_class(self, name)

    @_check_alive
    def interactive_ssh(self, command=None):
        if command is None:
            command = ['bash']

        return ssh.interactive_ssh(
            ip_addr=self.ip(),
            host_name=self.name(),
            ssh_key=self.virt_env.prefix.paths.ssh_id_rsa(),
            command=command,
            username=self._spec.get('ssh-user'),
            password=self._spec.get('ssh-password'),
        )

    def nics(self):
        return self._spec['nics'][:]

    def nets(self):
        return [nic['net'] for nic in self._spec['nics']]

    def distro(self):
        distro = self._spec.get('distro', None)
        if distro is None:
            distro = self._template_metadata().get('distro', None)

        return distro

    def root_password(self):
        root_password = self._spec.get('root-password', None)
        if root_password is None:
            root_password = self._spec.get('ssh-password', '')

        return root_password

    def collect_artifacts(self, host_path):
        self.extract_paths(
            [
                (
                    guest_path,
                    os.path.join(host_path, guest_path.replace('/', '_')),
                ) for guest_path in self._artifact_paths()
            ]
        )

    def guest_agent(self):
        if 'guest-agent' not in self._spec:
            for possible_name in ('qemu-guest-agent', 'qemu-ga'):
                try:
                    if self.service(possible_name).exists():
                        self._spec['guest-agent'] = possible_name
                        self.save()
                        break
                except RuntimeError as err:
                    raise RuntimeError(
                        'Could not find guest agent service: %s' % err
                    )
            else:
                raise RuntimeError('Could not find guest agent service')

        return self.service(self._spec['guest-agent'])

    def has_guest_agent(self):
        try:
            self.guest_agent()
        except RuntimeError:
            return False

        return True

    def _get_vm_provider(self):
        default_provider = config.get('default_vm_provider')
        provider_name = self._spec.get('vm-provider', default_provider)
        provider = self.vm_providers.get(provider_name)
        self._spec['vm-provider'] = provider_name
        return provider(vm=self)

    @classmethod
    def _normalize_spec(cls, spec):
        spec['snapshots'] = spec.get('snapshots', {})
        spec['metadata'] = spec.get('metadata', {})

        if 'root-password' not in spec:
            root_password = config.get('root_password')
            if root_password:
                spec['ssh-password'] = root_password
            else:
                spec['ssh-password'] = config.get('ssh_password')

        if 'ssh-user' not in spec:
            spec['ssh-user'] = config.get('ssh_user')

        return spec

    @contextlib.contextmanager
    def _scp(self):
        client = ssh.get_ssh_client(
            ip_addr=self.ip(),
            host_name=self.name(),
            ssh_key=self.virt_env.prefix.paths.ssh_id_rsa(),
            username=self._spec.get('ssh-user'),
            password=self._spec.get('ssh-password'),
        )
        scp = SCPClient(client.get_transport())
        try:
            yield scp
        finally:
            client.close()

    def _detect_service_provider(self):
        LOGGER.debug('Detecting service provider for %s', self.name())

        for provider_name, service_class in self.service_providers.items():
            if service_class.is_supported(self):
                LOGGER.debug(
                    'Setting %s as service provider for %s',
                    provider_name,
                    self.name(),
                )
                self._service_class = service_class
                self._spec['service_provider'] = provider_name
                self.save()
                return

        raise RuntimeError('No service provider detected for %s' % self.name())

    def _template_metadata(self):
        return self._spec['disks'][0].get('metadata', {})

    def _artifact_paths(self):
        return self._spec.get('artifacts', [])

    def _get_service_provider(self):
        """
        **NOTE**: Can be reduced to just one get call once we remove support
        for the service_class spec entry

        Returns:
            class: class for the loaded provider for that vm_spec
            None: if no provider was specified in the vm_spec
        """
        service_class = self._spec.get('service_class', None)
        if service_class is not None:
            warnings.warn(
                'The service_class key for a domain is deprecated, you should '
                'change it to service_provider instead'
            )
            service_provider = _resolve_service_class(
                class_name=service_class,
                service_providers=self.service_providers,
            )
        else:
            service_provider = self.service_providers.get(
                self._spec.get('service_provider', None),
                None,
            )

        return service_provider


def _resolve_service_class(class_name, service_providers):
    """
    **NOTE**: This must be remved once the service_class spec entry is fully
    deprecated

    Retrieves a service plugin class from the class name instead of the
    provider name

    Args:
        class_name(str): Class name of the service plugin to retrieve
        service_providers(dict): provider_name->provider_class of the loaded
            service providers

    Returns:
        class: Class of the plugin that matches that name

    Raises:
        lago.plugins.NoSuchPluginError: if there was no service plugin that
            matched the search
    """
    for plugin in service_providers.itervalues():
        if plugin.__class__.__name__ == class_name:
            return plugin

    raise plugins.NoSuchPluginError(
        'No service provider plugin with class name %s found, loaded '
        'providers: %s' % (
            class_name,
            [
                plugin.__class__.__name__
                for plugin in service_providers.itervalues()
            ],
        )
    )
