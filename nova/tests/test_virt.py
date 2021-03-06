# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 Isaku Yamahata
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import os

from nova import exception
from nova import flags
from nova import test
from nova import utils
from nova.virt.disk import api as disk_api
from nova.virt import driver

from nova.openstack.common import jsonutils

FLAGS = flags.FLAGS


class TestVirtDriver(test.TestCase):
    def test_block_device(self):
        swap = {'device_name': '/dev/sdb',
                'swap_size': 1}
        ephemerals = [{'num': 0,
                       'virtual_name': 'ephemeral0',
                       'device_name': '/dev/sdc1',
                       'size': 1}]
        block_device_mapping = [{'mount_device': '/dev/sde',
                                 'device_path': 'fake_device'}]
        block_device_info = {
                'root_device_name': '/dev/sda',
                'swap': swap,
                'ephemerals': ephemerals,
                'block_device_mapping': block_device_mapping}

        empty_block_device_info = {}

        self.assertEqual(
            driver.block_device_info_get_root(block_device_info), '/dev/sda')
        self.assertEqual(
            driver.block_device_info_get_root(empty_block_device_info), None)
        self.assertEqual(
            driver.block_device_info_get_root(None), None)

        self.assertEqual(
            driver.block_device_info_get_swap(block_device_info), swap)
        self.assertEqual(driver.block_device_info_get_swap(
            empty_block_device_info)['device_name'], None)
        self.assertEqual(driver.block_device_info_get_swap(
            empty_block_device_info)['swap_size'], 0)
        self.assertEqual(
            driver.block_device_info_get_swap({'swap': None})['device_name'],
            None)
        self.assertEqual(
            driver.block_device_info_get_swap({'swap': None})['swap_size'],
            0)
        self.assertEqual(
            driver.block_device_info_get_swap(None)['device_name'], None)
        self.assertEqual(
            driver.block_device_info_get_swap(None)['swap_size'], 0)

        self.assertEqual(
            driver.block_device_info_get_ephemerals(block_device_info),
            ephemerals)
        self.assertEqual(
            driver.block_device_info_get_ephemerals(empty_block_device_info),
            [])
        self.assertEqual(
            driver.block_device_info_get_ephemerals(None),
            [])

    def test_swap_is_usable(self):
        self.assertFalse(driver.swap_is_usable(None))
        self.assertFalse(driver.swap_is_usable({'device_name': None}))
        self.assertFalse(driver.swap_is_usable({'device_name': '/dev/sdb',
                                                'swap_size': 0}))
        self.assertTrue(driver.swap_is_usable({'device_name': '/dev/sdb',
                                                'swap_size': 1}))


class TestVirtDisk(test.TestCase):
    def setUp(self):
        super(TestVirtDisk, self).setUp()
        self.executes = []

        def fake_execute(*cmd, **kwargs):
            self.executes.append(cmd)
            return None, None

        self.stubs.Set(utils, 'execute', fake_execute)

    def test_lxc_destroy_container(self):

        def proc_mounts(self, mount_point):
            mount_points = {
                '/mnt/loop/nopart': '/dev/loop0',
                '/mnt/loop/part': '/dev/mapper/loop0p1',
                '/mnt/nbd/nopart': '/dev/nbd15',
                '/mnt/nbd/part': '/dev/mapper/nbd15p1',
                '/mnt/guestfs': 'guestmount',
            }
            return mount_points[mount_point]

        self.stubs.Set(os.path, 'exists', lambda _: True)
        self.stubs.Set(disk_api._DiskImage, '_device_for_path', proc_mounts)
        expected_commands = []

        disk_api.destroy_container('/mnt/loop/nopart')
        expected_commands += [
                              ('umount', '/dev/loop0'),
                              ('losetup', '--detach', '/dev/loop0'),
                             ]

        disk_api.destroy_container('/mnt/loop/part')
        expected_commands += [
                              ('umount', '/dev/mapper/loop0p1'),
                              ('kpartx', '-d', '/dev/loop0'),
                              ('losetup', '--detach', '/dev/loop0'),
                             ]

        disk_api.destroy_container('/mnt/nbd/nopart')
        expected_commands += [
                              ('umount', '/dev/nbd15'),
                              ('qemu-nbd', '-d', '/dev/nbd15'),
                             ]

        disk_api.destroy_container('/mnt/nbd/part')
        expected_commands += [
                              ('umount', '/dev/mapper/nbd15p1'),
                              ('kpartx', '-d', '/dev/nbd15'),
                              ('qemu-nbd', '-d', '/dev/nbd15'),
                             ]

        disk_api.destroy_container('/mnt/guestfs')
        expected_commands += [
                              ('fusermount', '-u', '/mnt/guestfs'),
                             ]
        # It's not worth trying to match the last timeout command
        self.executes.pop()

        self.assertEqual(self.executes, expected_commands)


class TestVirtDiskPaths(test.TestCase):
    def setUp(self):
        super(TestVirtDiskPaths, self).setUp()

        real_execute = utils.execute

        def nonroot_execute(*cmd_parts, **kwargs):
            kwargs.pop('run_as_root', None)
            return real_execute(*cmd_parts, **kwargs)

        self.stubs.Set(utils, 'execute', nonroot_execute)

    def test_check_safe_path(self):
        ret = disk_api._join_and_check_path_within_fs('/foo', 'etc',
                                                      'something.conf')
        self.assertEquals(ret, '/foo/etc/something.conf')

    def test_check_unsafe_path(self):
        self.assertRaises(exception.Invalid,
                          disk_api._join_and_check_path_within_fs,
                          '/foo', 'etc/../../../something.conf')

    def test_inject_files_with_bad_path(self):
        self.assertRaises(exception.Invalid,
                          disk_api._inject_file_into_fs,
                          '/tmp', '/etc/../../../../etc/passwd',
                          'hax')

    def test_inject_metadata(self):
        with utils.tempdir() as tmpdir:
            meta_objs = [{"key": "foo", "value": "bar"}]
            metadata = {"foo": "bar"}
            disk_api._inject_metadata_into_fs(meta_objs, tmpdir)
            json_file = os.path.join(tmpdir, 'meta.js')
            json_data = jsonutils.loads(open(json_file).read())
            self.assertEqual(metadata, json_data)
