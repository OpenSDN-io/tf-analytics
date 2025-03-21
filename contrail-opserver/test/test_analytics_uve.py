#!/usr/bin/python3

#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

#
# analytics_uvetest.py
#
# UVE and Alarm tests
#

import os
import signal
from gevent import monkey
monkey.patch_all()
import unittest
import testtools
import fixtures
import mock
import socket
from .utils.util import obj_to_dict, find_buildroot, \
     add_iptables_rule, delete_iptables_rule
from .utils.analytics_fixture import AnalyticsFixture
from .utils.generator_fixture import GeneratorFixture
import logging
import time
from opserver.sandesh.viz.constants import *
from opserver.sandesh.viz.constants import _OBJECT_TABLES
from opserver.vnc_cfg_api_client import VncCfgApiClient
from gevent import signal_handler as gevent_signal

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
builddir = find_buildroot(os.getcwd())


class AnalyticsUveTest(testtools.TestCase, fixtures.TestWithFixtures):

    @classmethod
    def setUpClass(cls):
        if (os.getenv('LD_LIBRARY_PATH', '').find('build/lib') < 0):
            if (os.getenv('DYLD_LIBRARY_PATH', '').find('build/lib') < 0):
                assert(False)

    @classmethod
    def tearDownClass(cls):
        pass
    
    def setUp(self):
        super(AnalyticsUveTest, self).setUp()
        mock_is_role_cloud_admin = mock.patch.object(VncCfgApiClient,
            'is_role_cloud_admin')
        mock_is_role_cloud_admin.return_value = True
        mock_is_role_cloud_admin.start()
        self.addCleanup(mock_is_role_cloud_admin.stop)
        mock_get_obj_perms_by_name = mock.patch.object(VncCfgApiClient,
            'get_obj_perms_by_name')
        rv_uve_perms = {'permissions': 'RWX'}
        mock_get_obj_perms_by_name.return_value = rv_uve_perms
        mock_get_obj_perms_by_name.start()
        self.addCleanup(mock_get_obj_perms_by_name.stop)

    #@unittest.skip('Skipping non-cassandra test with vizd')
    def test_00_nocassandra(self):
        '''
        This test starts redis,vizd,opserver and qed
        Then it checks that the collector UVE (via redis)
        can be accessed from opserver.
        '''
        logging.info("%%% test_00_nocassandra %%%")

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0)) 
        assert vizd_obj.verify_on_setup()

        return True
    # end test_00_nocassandra

    #@unittest.skip('Skipping VM UVE test')
    def test_01_vm_uve(self):
        '''
        This test starts redis, vizd, opserver, qed, and a python generator
        that simulates vrouter and sends UveVirtualMachineAgentTrace messages.
        Then it checks that the VM UVE (via redis) can be accessed from
        opserver.
        '''
        logging.info("%%% test_01_vm_uve %%%")

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0))
        assert vizd_obj.verify_on_setup()
        collectors = [vizd_obj.get_collector()]
        generator_obj = self.useFixture(
            GeneratorFixture("contrail-vrouter-agent", collectors,
                             logging, vizd_obj.get_opserver_port()))
        assert generator_obj.verify_on_setup()
        generator_obj.send_vm_uve(vm_id='abcd',
                                  num_vm_ifs=5,
                                  msg_count=5)
        assert generator_obj.verify_vm_uve(vm_id='abcd',
                                           num_vm_ifs=5,
                                           msg_count=5)
        # Delete the VM UVE and verify that the deleted flag is set
        # in the UVE cache
        generator_obj.delete_vm_uve('abcd')
        assert generator_obj.verify_vm_uve_cache(vm_id='abcd', delete=True)
        # Add the VM UVE with the same vm_id and verify that the deleted flag
        # is cleared in the UVE cache
        generator_obj.send_vm_uve(vm_id='abcd',
                                  num_vm_ifs=5,
                                  msg_count=5)
        assert generator_obj.verify_vm_uve_cache(vm_id='abcd')
        assert generator_obj.verify_vm_uve(vm_id='abcd',
                                           num_vm_ifs=5,
                                           msg_count=5)
        # Generate VM with vm_id containing XML control character
        generator_obj.send_vm_uve(vm_id='<abcd&>', num_vm_ifs=2, msg_count=2)
        assert generator_obj.verify_vm_uve(vm_id='<abcd&>', num_vm_ifs=2,
                                           msg_count=2)
        return True
    # end test_01_vm_uve

    #@unittest.skip('Skipping VM UVE test')
    def test_02_vm_uve_with_password(self):
        '''
        This test starts redis, vizd, opserver, qed, and a python generator
        that simulates vrouter and sends UveVirtualMachineAgentTrace messages.
        Then it checks that the VM UVE (via redis) can be accessed from
        opserver.
        '''
        logging.info("%%% test_02_vm_uve_with_password %%%")

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
                             redis_password='contrail'))
        assert vizd_obj.verify_on_setup()
        collectors = [vizd_obj.get_collector()]
        generator_obj = self.useFixture(
            GeneratorFixture("contrail-vrouter-agent", collectors,
                             logging, vizd_obj.get_opserver_port()))
        assert generator_obj.verify_on_setup()
        generator_obj.send_vm_uve(vm_id='abcd',
                                  num_vm_ifs=5,
                                  msg_count=5)
        assert generator_obj.verify_vm_uve(vm_id='abcd',
                                           num_vm_ifs=5,
                                           msg_count=5)
        return True
    # end test_02_vm_uve_with_password

    @unittest.skip('skipping verify redis-uve restart')
    def test_03_redis_uve_restart(self):
        logging.info('%%% test_03_redis_uve_restart %%%')

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
            start_kafka = True))
        assert vizd_obj.verify_on_setup()

        collectors = [vizd_obj.get_collector()]
        alarm_gen1 = self.useFixture(
            GeneratorFixture('vrouter-agent', collectors, logging,
                             None, hostname=socket.getfqdn("127.0.0.1")))
        alarm_gen1.verify_on_setup()

        # send vrouter UVE without build_info !!!
        # check for PartialSysinfo alarm
        alarm_gen1.send_vrouterinfo("myvrouter1")
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            "ObjectVRouter:myvrouter1",
            "default-global-system-config:partial-sysinfo-compute"))

        self.verify_uve_resync(vizd_obj)
 
        # Alarm should return after redis restart
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            "ObjectVRouter:myvrouter1",
            "default-global-system-config:partial-sysinfo-compute"))
    # end test_03_redis_uve_restart

    #@unittest.skip('verify redis-uve restart')
    def test_04_redis_uve_restart_with_password(self):
        logging.info('%%% test_04_redis_uve_restart_with_password %%%')

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging,
                             builddir, 0,
                             redis_password='contrail'))
        self.verify_uve_resync(vizd_obj)
        return True
    # end test_04_redis_uve_restart

    def verify_uve_resync(self, vizd_obj):
        assert vizd_obj.verify_on_setup()
        assert vizd_obj.verify_collector_redis_uve_connection(
                            vizd_obj.collectors[0])
        assert vizd_obj.verify_opserver_redis_uve_connection(
                            vizd_obj.opserver)
        # verify redis-uve list
        host = socket.getfqdn("127.0.0.1")
        gen_list = [host+':Analytics:contrail-collector:0',
                    host+':Database:contrail-query-engine:0',
                    host+':Analytics:contrail-analytics-api:0']
        assert vizd_obj.verify_generator_uve_list(gen_list)

        # stop redis-uve
        vizd_obj.redis_uves[0].stop()
        assert vizd_obj.verify_collector_redis_uve_connection(
                            vizd_obj.collectors[0], False)
        assert vizd_obj.verify_opserver_redis_uve_connection(
                            vizd_obj.opserver, False)
        # start redis-uve and verify that contrail-collector and Opserver are
        # connected to the redis-uve
        vizd_obj.redis_uves[0].start()
        assert vizd_obj.verify_collector_redis_uve_connection(
                            vizd_obj.collectors[0])
        assert vizd_obj.verify_opserver_redis_uve_connection(
                            vizd_obj.opserver)
        # verify that UVEs are resynced with redis-uve
        assert vizd_obj.verify_generator_uve_list(gen_list)

    #@unittest.skip('Skipping contrail-collector HA test')
    def test_05_collector_ha(self):
        logging.info('%%% test_05_collector_ha %%%')
        
        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
                             collector_ha_test=True))
        assert vizd_obj.verify_on_setup()
        collectors = [vizd_obj.collectors[1].get_addr(), 
                      vizd_obj.collectors[0].get_addr()]
        vr_agent = self.useFixture(
            GeneratorFixture("contrail-vrouter-agent", collectors,
                             logging, vizd_obj.get_opserver_port()))
        assert vr_agent.verify_on_setup()
        source = socket.getfqdn("127.0.0.1")
        exp_genlist = [
            source+':Analytics:contrail-collector:0',
            source+':Analytics:contrail-analytics-api:0',
            source+':Database:contrail-query-engine:0',
            source+':Test:contrail-vrouter-agent:0',
            source+'dup:Analytics:contrail-collector:0'
        ]
        assert vizd_obj.verify_generator_list(vizd_obj.collectors,
                                              exp_genlist)
        # stop collectors[0] and verify that all the generators are connected
        # to collectors[1]
        vizd_obj.collectors[0].stop()
        exp_genlist = [
            source+'dup:Analytics:contrail-collector:0',
            source+':Analytics:contrail-analytics-api:0',
            source+':Database:contrail-query-engine:0',
            source+':Test:contrail-vrouter-agent:0'
        ]
        assert vizd_obj.verify_generator_list([vizd_obj.collectors[1]],
                                              exp_genlist)
        # start collectors[0]
        vizd_obj.collectors[0].start()
        exp_genlist = [source+':Analytics:contrail-collector:0']
        assert vizd_obj.verify_generator_list([vizd_obj.collectors[0]],
                                              exp_genlist)
        # verify that the old UVEs are flushed from redis when collector restarts
        exp_genlist = [vizd_obj.collectors[0].get_generator_id()]
        assert vizd_obj.verify_generator_list_in_redis(\
                                vizd_obj.collectors[0].get_redis_uve(),
                                exp_genlist)

        # stop collectors[1] and verify that all the generators are connected
        # to collectors[0]
        vizd_obj.collectors[1].stop()
        exp_genlist = [
            source+':Analytics:contrail-collector:0',
            source+':Analytics:contrail-analytics-api:0',
            source+':Database:contrail-query-engine:0',
            source+':Test:contrail-vrouter-agent:0'
        ]
        assert vizd_obj.verify_generator_list([vizd_obj.collectors[0]],
                                              exp_genlist)
        # verify the generator list in redis
        exp_genlist = [vizd_obj.collectors[0].get_generator_id(),
                       vr_agent.get_generator_id(),
                       vizd_obj.opserver.get_generator_id(),
                       vizd_obj.query_engine.get_generator_id()]
        assert vizd_obj.verify_generator_list_in_redis(\
                                vizd_obj.collectors[0].get_redis_uve(),
                                exp_genlist)

        # stop QE 
        vizd_obj.query_engine.stop()
        exp_genlist = [
            source+':Analytics:contrail-collector:0',
            source+':Analytics:contrail-analytics-api:0',
            source+':Test:contrail-vrouter-agent:0'
        ]
        assert vizd_obj.verify_generator_list([vizd_obj.collectors[0]],
                                              exp_genlist)

        # verify the generator list in redis
        exp_genlist = [vizd_obj.collectors[0].get_generator_id(),
                       vizd_obj.opserver.get_generator_id(),
                       vr_agent.get_generator_id()]
        assert vizd_obj.verify_generator_list_in_redis(\
                                vizd_obj.collectors[0].get_redis_uve(),
                                exp_genlist)

        # start a python generator and QE and verify that they are connected
        # to collectors[0]
        vr2_collectors = [vizd_obj.collectors[1].get_addr(), 
                          vizd_obj.collectors[0].get_addr()]
        vr2_agent = self.useFixture(
            GeneratorFixture("tf-snmp-collector", collectors,
                             logging, vizd_obj.get_opserver_port()))
        assert vr2_agent.verify_on_setup()
        vizd_obj.query_engine.start()
        exp_genlist = [
            source+':Analytics:contrail-collector:0',
            source+':Analytics:contrail-analytics-api:0',
            source+':Test:contrail-vrouter-agent:0',
            source+':Database:contrail-query-engine:0',
            source+':Test:tf-snmp-collector:0'
        ]
        assert vizd_obj.verify_generator_list([vizd_obj.collectors[0]],
                                              exp_genlist)
        # stop the collectors[0] - both collectors[0] and collectors[1] are down
        # send the VM UVE and verify that the VM UVE is synced after connection
        # to the collector
        vizd_obj.collectors[0].stop()
        # Make sure the connection to the collector is teared down before 
        # sending the VM UVE
        while True:
            if vr_agent.verify_on_setup() is False:
                break
        vr_agent.send_vm_uve(vm_id='abcd-1234-efgh-5678',
                             num_vm_ifs=5, msg_count=5) 
        vizd_obj.collectors[1].start()
        exp_genlist = [
            source+'dup:Analytics:contrail-collector:0',
            source+':Analytics:contrail-analytics-api:0',
            source+':Test:contrail-vrouter-agent:0',
            source+':Database:contrail-query-engine:0',
            source+':Test:tf-snmp-collector:0'
        ]
        assert vizd_obj.verify_generator_list([vizd_obj.collectors[1]],
                                              exp_genlist)
        assert vr_agent.verify_vm_uve(vm_id='abcd-1234-efgh-5678',
                                      num_vm_ifs=5, msg_count=5)
    # end test_05_collector_ha

    #@unittest.skip('Skipping AlarmGen basic test')
    def test_06_alarmgen_basic(self):
        '''
        This test starts the analytics processes.
        It enables partition 0 on alarmgen, and confirms
        that it got enabled
        '''
        logging.info("%%% test_06_alarmgen_basic %%%")

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
            start_kafka = True))
        assert vizd_obj.verify_on_setup()

        assert(vizd_obj.verify_uvetable_alarm("ObjectAnalyticsAlarmInfo",
            "ObjectAnalyticsAlarmInfo:" + socket.getfqdn("127.0.0.1"),
            "default-global-system-config:process-status"))
        # setup generator for sending Vrouter build_info
        collector = vizd_obj.collectors[0].get_addr()
        alarm_gen1 = self.useFixture(
            GeneratorFixture('vrouter-agent', [collector], logging,
                             None, hostname=socket.getfqdn("127.0.0.1")))
        alarm_gen1.verify_on_setup()

        # send vrouter UVE without build_info !!!
        # check for PartialSysinfo alarm
        alarm_gen1.send_vrouterinfo("myvrouter1")
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            "ObjectVRouter:myvrouter1",
            "default-global-system-config:partial-sysinfo-compute",
            rules=[{"and_list": [{
                "condition": {
                    "operation": "==",
                    "operand1": "ObjectVRouter.build_info",
                    "operand2": {
                        "json_value": "null"
                    }
                },
                "match": [{"json_operand1_value": "null"}]
            }]}]
        ))

        # Now try to clear the alarm by sending build_info
        alarm_gen1.send_vrouterinfo("myvrouter1", b_info = True)
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            "ObjectVRouter:myvrouter1",
            "default-global-system-config:partial-sysinfo-compute",
            is_set=False))

        # send vrouter UVE without build_info !!!
        # check for PartialSysinfo alarm
        alarm_gen1.send_vrouterinfo("myvrouter1", deleted = True)
        alarm_gen1.send_vrouterinfo("myvrouter1")
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            "ObjectVRouter:myvrouter1",
            "default-global-system-config:partial-sysinfo-compute"))

        # Now try to clear the alarm by deleting the UVE
        alarm_gen1.send_vrouterinfo("myvrouter1", deleted = True)
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            "ObjectVRouter:myvrouter1",
            "default-global-system-config:partial-sysinfo-compute",
            is_set=False))

        alarm_gen2 = self.useFixture(
            GeneratorFixture('vrouter-agent', [collector], logging,
                             None, hostname=socket.getfqdn("127.0.0.1"), inst = "1"))
        alarm_gen2.verify_on_setup()

        # send vrouter UVE without build_info !!!
        # check for PartialSysinfo alarm
        alarm_gen2.send_vrouterinfo("myvrouter2")
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            "ObjectVRouter:myvrouter2",
            "default-global-system-config:partial-sysinfo-compute"))

        # Now try to clear the alarm by disconnecting the generator
        alarm_gen2._sandesh_instance._client._connection.set_admin_state(\
            down=True)
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            "ObjectVRouter:myvrouter2",
            "default-global-system-config:partial-sysinfo-compute",
            is_set=False))
         
        # send vrouter UVE of myvrouter without build_info again !!!
        # check for PartialSysinfo alarm
        alarm_gen1.send_vrouterinfo("myvrouter1")
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            "ObjectVRouter:myvrouter1",
            "default-global-system-config:partial-sysinfo-compute"))

        # Verify that we can give up partition ownership 
        assert(vizd_obj.set_alarmgen_partition(0,0) == 'true')
        assert(vizd_obj.verify_alarmgen_partition(0,'false'))

        # Give up the other partitions
        assert(vizd_obj.set_alarmgen_partition(1,0) == 'true')
        assert(vizd_obj.set_alarmgen_partition(2,0) == 'true')
        assert(vizd_obj.set_alarmgen_partition(3,0) == 'true')

        # Confirm that alarms are all gone
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            None, None))

        # Get the partitions again
        assert(vizd_obj.set_alarmgen_partition(0,1) == 'true')
        assert(vizd_obj.set_alarmgen_partition(1,1) == 'true')
        assert(vizd_obj.set_alarmgen_partition(2,1) == 'true')
        assert(vizd_obj.set_alarmgen_partition(3,1) == 'true')
        assert(vizd_obj.verify_alarmgen_partition(0,'true'))

        # The PartialSysinfo alarm on myvrouter should return
        assert(vizd_obj.verify_uvetable_alarm("ObjectVRouter",
            "ObjectVRouter:myvrouter1",
            "default-global-system-config:partial-sysinfo-compute"))

        return True
    # end test_06_alarmgen_basic

    #@unittest.skip('Skipping Alarm test')
    def test_07_alarm(self):
        '''
        This test starts redis, collectors, analytics-api and
        python generators that simulates alarm generator. This
        test sends alarms from alarm generators and verifies the
        retrieval of alarms from analytics-api.
        '''
        logging.info('%%% test_07_alarm %%%')

        # collector_ha_test flag is set to True, because we wanna test
        # retrieval of alarms across multiple redis servers.
        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
                             collector_ha_test=True,
                             start_kafka = True))
        assert vizd_obj.verify_on_setup()

        # create alarm-generator and attach it to the first collector.
        collectors = [vizd_obj.collectors[0].get_addr(), 
                      vizd_obj.collectors[1].get_addr()]
        alarm_gen1 = self.useFixture(
            GeneratorFixture('contrail-alarm-gen', [collectors[0]], logging,
                             None, hostname=socket.getfqdn("127.0.0.1")+'_1'))
        alarm_gen1.verify_on_setup()

        # send process state alarm for analytics-node
        alarms = alarm_gen1.create_process_state_alarm(
                    'contrail-query-engine')
        alarm_gen1.send_alarm(socket.getfqdn("127.0.0.1")+'_1', alarms,
                              COLLECTOR_INFO_TABLE)
        analytics_tbl = _OBJECT_TABLES[COLLECTOR_INFO_TABLE].log_query_name

        # send proces state alarm for control-node
        alarms = alarm_gen1.create_process_state_alarm('contrail-dns')
        alarm_gen1.send_alarm('<&'+socket.getfqdn("127.0.0.1")+'_1>', alarms,
                              BGP_ROUTER_TABLE)
        control_tbl = _OBJECT_TABLES[BGP_ROUTER_TABLE].log_query_name

        # create another alarm-generator and attach it to the second collector.
        alarm_gen2 = self.useFixture(
            GeneratorFixture('contrail-alarm-gen', [collectors[1]], logging,
                             None, hostname=socket.getfqdn("127.0.0.1")+'_2'))
        alarm_gen2.verify_on_setup()
        
        # send process state alarm for analytics-node
        alarms = alarm_gen2.create_process_state_alarm(
                    'tf-topology')
        alarm_gen2.send_alarm(socket.getfqdn("127.0.0.1")+'_2', alarms,
                              COLLECTOR_INFO_TABLE)

        keys = [socket.getfqdn("127.0.0.1")+'_1', socket.getfqdn("127.0.0.1")+'_2']
        assert(vizd_obj.verify_alarm_list_include(analytics_tbl,
                                          expected_alarms=keys))
        assert(vizd_obj.verify_alarm(analytics_tbl, keys[0], obj_to_dict(
            alarm_gen1.alarms[COLLECTOR_INFO_TABLE][keys[0]].data)))
        assert(vizd_obj.verify_alarm(analytics_tbl, keys[1], obj_to_dict(
            alarm_gen2.alarms[COLLECTOR_INFO_TABLE][keys[1]].data)))

        keys = ['<&'+socket.getfqdn("127.0.0.1")+'_1>']
        assert(vizd_obj.verify_alarm_list_include(control_tbl, expected_alarms=keys))
        assert(vizd_obj.verify_alarm(control_tbl, keys[0], obj_to_dict(
            alarm_gen1.alarms[BGP_ROUTER_TABLE][keys[0]].data)))

        # delete analytics-node alarm generated by alarm_gen2
        alarm_gen2.delete_alarm(socket.getfqdn("127.0.0.1")+'_2',
                                COLLECTOR_INFO_TABLE)

        # verify analytics-node alarms
        keys = [socket.getfqdn("127.0.0.1")+'_1']
        assert(vizd_obj.verify_alarm_list_include(analytics_tbl,
            expected_alarms=keys))
        ukeys = [socket.getfqdn("127.0.0.1")+'_2']
        assert(vizd_obj.verify_alarm_list_exclude(analytics_tbl,
            unexpected_alms=ukeys))
        assert(vizd_obj.verify_alarm(analytics_tbl, keys[0], obj_to_dict(
            alarm_gen1.alarms[COLLECTOR_INFO_TABLE][keys[0]].data)))
        assert(vizd_obj.verify_alarm(analytics_tbl, ukeys[0], {}))
       
        # Disconnect alarm_gen1 from Collector and verify that all
        # alarms generated by alarm_gen1 is removed by the Collector. 
        alarm_gen1.disconnect_from_collector()
        ukeys = [socket.getfqdn("127.0.0.1")+'_1']
        assert(vizd_obj.verify_alarm_list_exclude(analytics_tbl,
            unexpected_alms=ukeys))
        assert(vizd_obj.verify_alarm(analytics_tbl, ukeys[0], {}))

        ukeys = ['<&'+socket.getfqdn("127.0.0.1")+'_1']
        assert(vizd_obj.verify_alarm_list_exclude(control_tbl,
            unexpected_alms=ukeys))
        assert(vizd_obj.verify_alarm(control_tbl, ukeys[0], {}))

        # update analytics-node alarm in disconnect state
        alarms = alarm_gen1.create_process_state_alarm(
                    'tf-snmp-collector')
        alarm_gen1.send_alarm(socket.getfqdn("127.0.0.1")+'_1', alarms,
                              COLLECTOR_INFO_TABLE)
        
        # Connect alarm_gen1 to Collector and verify that all
        # alarms generated by alarm_gen1 is synced with Collector.
        alarm_gen1.connect_to_collector()
        keys = [socket.getfqdn("127.0.0.1")+'_1']
        assert(vizd_obj.verify_alarm_list_include(analytics_tbl, 
            expected_alarms=keys))
        assert(vizd_obj.verify_alarm(analytics_tbl, keys[0], obj_to_dict(
            alarm_gen1.alarms[COLLECTOR_INFO_TABLE][keys[0]].data)))
        
        keys = ['<&'+socket.getfqdn("127.0.0.1")+'_1>']
        assert(vizd_obj.verify_alarm_list_include(control_tbl,
            expected_alarms=keys))
        assert(vizd_obj.verify_alarm(control_tbl, keys[0], obj_to_dict(
            alarm_gen1.alarms[BGP_ROUTER_TABLE][keys[0]].data)))
    # end test_07_alarm

    #@unittest.skip('Skipping UVE/Alarm Filter test')
    def test_08_uve_alarm_filter(self):
        '''
        This test verifies the filter options kfilt, sfilt, mfilt and cfilt
        in the UVE/Alarm GET and POST methods.
        '''
        logging.info('%%% test_08_uve_alarm_filter %%%')

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
                collector_ha_test=True, start_kafka = True))
        assert vizd_obj.verify_on_setup()

        collectors = [vizd_obj.collectors[0].get_addr(),
                      vizd_obj.collectors[1].get_addr()]
        api_server_name = socket.getfqdn("127.0.0.1")+'_1'
        api_server = self.useFixture(
            GeneratorFixture('contrail-api', [collectors[0]], logging,
                             None, node_type='Config',
                             hostname=api_server_name))
        vr_agent_name = socket.getfqdn("127.0.0.1")+'_2'
        vr_agent = self.useFixture(
            GeneratorFixture('contrail-vrouter-agent', [collectors[1]],
                             logging, None, node_type='Compute',
                             hostname=vr_agent_name))
        alarm_gen1_name = socket.getfqdn("127.0.0.1")+'_1'
        alarm_gen1 = self.useFixture(
            GeneratorFixture('contrail-alarm-gen', [collectors[0]], logging,
                             None, node_type='Analytics',
                             hostname=alarm_gen1_name))
        alarm_gen2_name = socket.getfqdn("127.0.0.1")+'_3'
        alarm_gen2 = self.useFixture(
            GeneratorFixture('contrail-alarm-gen', [collectors[1]], logging,
                             None, node_type='Analytics',
                             hostname=alarm_gen2_name))
        api_server.verify_on_setup()
        vr_agent.verify_on_setup()
        alarm_gen1.verify_on_setup()
        alarm_gen2.verify_on_setup()

        vn_list = ['default-domain:project1:vn1',
                   'default-domain:project1:vn2',
                   'default-domain:project2:vn1',
                   'default-domain:project2:vn1&']
        # generate UVEs for the filter test
        api_server.send_vn_config_uve(name=vn_list[0],
                                      partial_conn_nw=[vn_list[1]],
                                      num_acl_rules=2)
        api_server.send_vn_config_uve(name=vn_list[1],
                                      num_acl_rules=3)
        vr_agent.send_vn_agent_uve(name=vn_list[1], num_acl_rules=3,
                                   ipkts=2, ibytes=1024)
        vr_agent.send_vn_agent_uve(name=vn_list[2], ipkts=4, ibytes=128)
        vr_agent.send_vn_agent_uve(name=vn_list[3], ipkts=8, ibytes=256)
        # generate Alarms for the filter test
        alarms = alarm_gen1.create_alarm('InPktsThreshold')
        alarms += alarm_gen1.create_alarm('InBytesThreshold', ack=True)
        alarm_gen1.send_alarm(vn_list[1], alarms, VN_TABLE)
        alarms = alarm_gen2.create_alarm('ConfigNotPresent', ack=False)
        alarm_gen2.send_alarm(vn_list[2], alarms, VN_TABLE)
        alarms = alarm_gen2.create_alarm('ConfigNotPresent', ack=False)
        alarm_gen2.send_alarm(vn_list[3], alarms, VN_TABLE)

        filt_test = [
            # no filter
            {
                'uve_list_get': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'get_alarms': {
                    'virtual-network': [
                         {  'name' : 'default-domain:project1:vn2',
                            'value' : { 'UVEAlarms': { 
                                'alarms': [
                                    {
                                        'type': 'InPktsThreshold',
                                    },
                                    {
                                        'type': 'InBytesThreshold',
                                        'ack': True
                                    }
                                ]
                            } }
                         },
                         {  'name' : 'default-domain:project2:vn1',
                            'value' : { 'UVEAlarms': {
                                'alarms': [
                                    {
                                        'type': 'ConfigNotPresent',
                                        'ack': False
                                    }
                                ]
                            } }
                         },
                         {  'name' : 'default-domain:project2:vn1&',
                            'value' : { 'UVEAlarms': { 
                                'alarms': [
                                    {
                                        'type': 'ConfigNotPresent',
                                        'ack': False
                                    }
                                ]
                            } }
                         },
                     ]
                },
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn1',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'partially_connected_networks': [
                                        'default-domain:project1:vn2'
                                    ],
                                    'total_acl_rules': 2
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 2,
                                    'in_bytes': 1024,
                                    'total_acl_rules': 3
                                },
                                'UveVirtualNetworkConfig': {
                                    'total_acl_rules': 3
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'InPktsThreshold',
                                        },
                                        {
                                            'type': 'InBytesThreshold',
                                            'ack': True
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                    'in_bytes': 128
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                    'in_bytes': 256
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },

            # kfilt
            {
                'kfilt': ['*'],
                'uve_list_get': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn1',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'partially_connected_networks': [
                                        'default-domain:project1:vn2'
                                    ],
                                    'total_acl_rules': 2
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 2,
                                    'in_bytes': 1024,
                                    'total_acl_rules': 3
                                },
                                'UveVirtualNetworkConfig': {
                                    'total_acl_rules': 3
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'InPktsThreshold',
                                        },
                                        {
                                            'type': 'InBytesThreshold',
                                            'ack': True
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                    'in_bytes': 128
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                    'in_bytes': 256
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },
            {
                'kfilt': ['default-domain:project1:*',
                          'default-domain:project2:*'],
                'uve_list_get': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn1',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'partially_connected_networks': [
                                        'default-domain:project1:vn2'
                                    ],
                                    'total_acl_rules': 2
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 2,
                                    'in_bytes': 1024,
                                    'total_acl_rules': 3
                                },
                                'UveVirtualNetworkConfig': {
                                    'total_acl_rules': 3
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'InPktsThreshold',
                                        },
                                        {
                                            'type': 'InBytesThreshold',
                                            'ack': True
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                    'in_bytes': 128
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                    'in_bytes': 256
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },
            {
                'kfilt': ['default-domain:project1:vn1',
                          'default-domain:project2:*'],
                'uve_list_get': [
                    'default-domain:project1:vn1',
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn1',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'partially_connected_networks': [
                                        'default-domain:project1:vn2'
                                    ],
                                    'total_acl_rules': 2
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                    'in_bytes': 128
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                    'in_bytes': 256
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },
            {
                'kfilt': [
                    'default-domain:project2:*',
                    'invalid-vn:*'
                ],
                'get_alarms': {
                    'virtual-network': [
                         {  'name' : 'default-domain:project2:vn1',
                            'value' : { 'UVEAlarms': { 
                                'alarms': [
                                    {
                                        'type': 'ConfigNotPresent',
                                        'ack': False
                                    }
                                ]
                            } }
                         },
                         {  'name' : 'default-domain:project2:vn1&',
                            'value' : { 'UVEAlarms': {
                                'alarms': [
                                    {
                                        'type': 'ConfigNotPresent',
                                        'ack': False
                                    }
                                ]
                            } }
                         },
                     ]
                },
                'uve_list_get': [
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                    'in_bytes': 128
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                    'in_bytes': 256
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },
            {
                'kfilt': [
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1&',
                    'invalid-vn'
                ],
                'uve_list_get': [
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 2,
                                    'in_bytes': 1024,
                                    'total_acl_rules': 3
                                },
                                'UveVirtualNetworkConfig': {
                                    'total_acl_rules': 3
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'InPktsThreshold',
                                        },
                                        {
                                            'type': 'InBytesThreshold',
                                            'ack': True
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                    'in_bytes': 256
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },
            {
                'kfilt': ['invalid-vn'],
                'uve_list_get': [],
                'uve_get_post': {'value': []},
            },

            # sfilt
            {
                'sfilt': socket.getfqdn("127.0.0.1")+'_1',
                'uve_list_get': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn1',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'partially_connected_networks': [
                                        'default-domain:project1:vn2'
                                    ],
                                    'total_acl_rules': 2
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'total_acl_rules': 3
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'InPktsThreshold',
                                        },
                                        {
                                            'type': 'InBytesThreshold',
                                            'ack': True
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },
            {
                'sfilt': socket.getfqdn("127.0.0.1")+'_3',
                'uve_list_get': [
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },
            {
                'sfilt': 'invalid_source',
                'uve_list_get': [],
                'uve_get_post': {'value': []},
            },

            # mfilt
            {
                'mfilt': 'Config:contrail-api:0',
                'uve_list_get': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn1',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'partially_connected_networks': [
                                        'default-domain:project1:vn2'
                                    ],
                                    'total_acl_rules': 2
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'total_acl_rules': 3
                                }
                            }
                        }
                    ]
                },
            },
            {
                'mfilt': 'Analytics:contrail-alarm-gen:0',
                'uve_list_get': [
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'InPktsThreshold',
                                        },
                                        {
                                            'type': 'InBytesThreshold',
                                            'ack': True
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },
            {
                'mfilt': 'Analytics:contrail-invalid:0',
                'uve_list_get': [],
                'uve_get_post': {'value': []},
            },

            # cfilt
            {
                'cfilt': ['UveVirtualNetworkAgent'],
                'uve_list_get': [
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 2,
                                    'in_bytes': 1024,
                                    'total_acl_rules': 3
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                    'in_bytes': 128
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                    'in_bytes': 256
                                }
                            }
                        }
                    ]
                },
            },
            {
                'cfilt': [
                    'UveVirtualNetworkAgent:total_acl_rules',
                    'UveVirtualNetworkConfig:partially_connected_networks'
                ],
                'uve_list_get': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn1',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'partially_connected_networks': [
                                        'default-domain:project1:vn2'
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'total_acl_rules': 3
                                }
                            }
                        }
                    ]
                },
            },
            {
                'cfilt': [
                    'UveVirtualNetworkConfig:invalid',
                    'UveVirtualNetworkAgent:in_tpkts',
                    'UVEAlarms:alarms'
                ],
                'uve_list_get': [
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 2,
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'InPktsThreshold',
                                        },
                                        {
                                            'type': 'InBytesThreshold',
                                            'ack': True
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },
            {
                'cfilt': [
                    'UveVirtualNetworkAgent:invalid',
                    'UVEAlarms:invalid_alarms',
                    'invalid'
                ],
                'uve_list_get': [],
                'uve_get_post': {'value': []},
            },

            # ackfilt
            {
                'ackfilt': True,
                'uve_list_get': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn1',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'partially_connected_networks': [
                                        'default-domain:project1:vn2'
                                    ],
                                    'total_acl_rules': 2
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 2,
                                    'in_bytes': 1024,
                                    'total_acl_rules': 3
                                },
                                'UveVirtualNetworkConfig': {
                                    'total_acl_rules': 3
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'InBytesThreshold',
                                            'ack': True
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                    'in_bytes': 128
                                },
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                    'in_bytes': 256
                                },
                            }
                        }
                    ]
                },
            },
            {
                'ackfilt': False,
                'uve_list_get': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'get_alarms': {
                    'virtual-network': [
                         {  'name' : 'default-domain:project1:vn2',
                            'value' : { 'UVEAlarms': { 
                                'alarms': [
                                    {
                                        'type': 'InPktsThreshold',
                                    },
                                ]
                            } }
                         },
                         {  'name' : 'default-domain:project2:vn1',
                            'value' : { 'UVEAlarms': { 
                                'alarms': [
                                    {
                                        'type': 'ConfigNotPresent',
                                        'ack': False
                                    }
                                ]
                            } }
                         },
                         {  'name' : 'default-domain:project2:vn1&',
                            'value' : { 'UVEAlarms': {
                                'alarms': [
                                    {
                                        'type': 'ConfigNotPresent',
                                        'ack': False
                                    }
                                ]
                            } }
                         },
                     ]
                },
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn1',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'partially_connected_networks': [
                                        'default-domain:project1:vn2'
                                    ],
                                    'total_acl_rules': 2
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 2,
                                    'in_bytes': 1024,
                                    'total_acl_rules': 3
                                },
                                'UveVirtualNetworkConfig': {
                                    'total_acl_rules': 3
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'InPktsThreshold',
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                    'in_bytes': 128
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                    'in_bytes': 256
                                },
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },

            # kfilt + sfilt
            {
                'kfilt': [
                    'default-domain:project1:*',
                    'default-domain:project2:vn1',
                    'default-domain:invalid'
                ],
                'sfilt': socket.getfqdn("127.0.0.1")+'_2',
                'uve_list_get': [
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 2,
                                    'in_bytes': 1024,
                                    'total_acl_rules': 3
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                    'in_bytes': 128
                                }
                            }
                        }
                    ]
                },
            },

            # kfilt + sfilt + ackfilt
            {
                'kfilt': [
                    'default-domain:project1:vn1',
                    'default-domain:project2:*',
                    'default-domain:invalid'
                ],
                'sfilt': socket.getfqdn("127.0.0.1")+'_2',
                'ackfilt': True,
                'uve_list_get': [
                    'default-domain:project2:vn1',
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 4,
                                    'in_bytes': 128
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project2:vn1&',
                            'value': {
                                'UveVirtualNetworkAgent': {
                                    'in_tpkts': 8,
                                    'in_bytes': 256
                                }
                            }
                        }
                    ]
                },
            },

            # kfilt + sfilt + cfilt
            {
                'kfilt': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2',
                    'default-domain:project2:vn1'
                ],
                'sfilt': socket.getfqdn("127.0.0.1")+'_1',
                'cfilt': [
                    'UveVirtualNetworkAgent',
                    'UVEAlarms',
                    'UveVirtualNetworkConfig:Invalid'
                ],
                'uve_list_get': [
                    'default-domain:project1:vn2'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'InPktsThreshold',
                                        },
                                        {
                                            'type': 'InBytesThreshold',
                                            'ack': True
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },

            # kfilt + mfilt + cfilt
            {
                'kfilt': ['*'],
                'mfilt': 'Config:contrail-api:0',
                'cfilt': [
                    'UveVirtualNetworkAgent',
                    'UVEAlarms:alarms'
                ],
                'uve_list_get': [],
                'uve_get_post': {'value': []},
            },

            # kfilt + sfilt + mfilt + cfilt
            {
                'kfilt': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2',
                    'default-domain:project2:*'
                ],
                'sfilt': socket.getfqdn("127.0.0.1")+'_1',
                'mfilt': 'Config:contrail-api:0',
                'cfilt': [
                    'UveVirtualNetworkConfig:partially_connected_networks',
                    'UveVirtualNetworkConfig:total_acl_rules',
                    'UVEAlarms'
                ],
                'uve_list_get': [
                    'default-domain:project1:vn1',
                    'default-domain:project1:vn2'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project1:vn1',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'partially_connected_networks': [
                                        'default-domain:project1:vn2'
                                    ],
                                    'total_acl_rules': 2
                                }
                            }
                        },
                        {
                            'name': 'default-domain:project1:vn2',
                            'value': {
                                'UveVirtualNetworkConfig': {
                                    'total_acl_rules': 3
                                },
                            }
                        }
                    ]
                },
            },
            {
                'kfilt': [
                    'default-domain:project1:*',
                    'default-domain:project2:vn1',
                    'default-domain:project2:invalid'
                ],
                'sfilt': socket.getfqdn("127.0.0.1")+'_3',
                'mfilt': 'Analytics:contrail-alarm-gen:0',
                'cfilt': [
                    'UveVirtualNetworkConfig',
                    'UVEAlarms:alarms',
                    'UveVirtualNetworkAgent'
                ],
                'uve_list_get': [
                    'default-domain:project2:vn1'
                ],
                'uve_get_post': {
                    'value': [
                        {
                            'name': 'default-domain:project2:vn1',
                            'value': {
                                'UVEAlarms': {
                                    'alarms': [
                                        {
                                            'type': 'ConfigNotPresent',
                                            'ack': False
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                },
            },

            # kfilt + sfilt + mfilt + cfilt + ackfilt
            {
                'kfilt': [
                    'default-domain:project1:*',
                    'default-domain:project2:vn1&',
                    'default-domain:project2:invalid'
                ],
                'sfilt': socket.getfqdn("127.0.0.1")+'_3',
                'mfilt': 'Analytics:contrail-alarm-gen:0',
                'cfilt': [
                    'UveVirtualNetworkConfig',
                    'UVEAlarms:alarms',
                    'UveVirtualNetworkAgent'
                ],
                'ackfilt': True,
                'uve_list_get': [
                    'default-domain:project2:vn1&'
                ],
                'uve_get_post': {'value': []},
            }
        ]

        vn_table = _OBJECT_TABLES[VN_TABLE].log_query_name

        for i in range(len(filt_test)):
            filters = dict(kfilt=filt_test[i].get('kfilt'),
                           sfilt=filt_test[i].get('sfilt'),
                           mfilt=filt_test[i].get('mfilt'),
                           cfilt=filt_test[i].get('cfilt'),
                           ackfilt=filt_test[i].get('ackfilt'))
            assert(vizd_obj.verify_uve_list(vn_table,
                filts=filters, exp_uve_list=filt_test[i]['uve_list_get']))
            assert(vizd_obj.verify_multi_uve_get(vn_table,
                filts=filters, exp_uves=filt_test[i]['uve_get_post']))
            assert(vizd_obj.verify_uve_post(vn_table,
                filts=filters, exp_uves=filt_test[i]['uve_get_post']))
            if 'get_alarms' in filt_test[i]:
                filters['tablefilt'] = 'virtual-network'
                assert(vizd_obj.verify_get_alarms(vn_table,
                    filts=filters, exp_uves=filt_test[i]['get_alarms']))
    # end test_08_uve_alarm_filter

    #@unittest.skip('Skipping UVE timestamp test')
    def test_09_uve_timestamp(self):
        '''
        This test verifies uve timestamp.
        '''
        logging.info('%%% test_09_uve_timestamp %%%')

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
                collector_ha_test=True, start_kafka = True))
        assert vizd_obj.verify_on_setup()
        collectors = [vizd_obj.collectors[0].get_addr(),
                      vizd_obj.collectors[1].get_addr()]
        vr_agent_1_name = socket.getfqdn("127.0.0.1")+'_1'
        vr_agent_1 = self.useFixture(
            GeneratorFixture('contrail-vrouter-agent', [collectors[0]], logging,
                             None, node_type='Compute',
                             hostname=vr_agent_1_name))
        vr_agent_2_name = socket.getfqdn("127.0.0.1")+'_2'
        vr_agent_2 = self.useFixture(
            GeneratorFixture('contrail-vrouter-agent', [collectors[1]],
                             logging, None, node_type='Compute',
                             hostname=vr_agent_2_name))
        vr_agent_1.verify_on_setup()
        vr_agent_2.verify_on_setup()

        vn_list = ['default-domain:project1:vn1']
        # generate UVEs for the filter test
        vr_agent_1.send_vn_agent_uve(name=vn_list[0], ipkts=4, ibytes=128)
        vr_agent_2.send_vn_agent_uve(name=vn_list[0], ipkts=8, ibytes=256)

        table = 'virtual-network/' + vn_list[0]
        assert(vizd_obj.verify_uve_timestamp(table, 'UveVirtualNetworkAgent', 2))
    # end test_09_uve_timestamp

    #
    #               disk                compaction
    #              usage                tasks
    #                       |
    #                       |
    #              90   (severity=0)    400
    #                     LEVEL 0
    #              85   (severity=1)    300
    #                       |
    #                       |
    #                       |
    #                       |
    #              80   (severity=3)    200
    #                     LEVEL 1
    #              75   (severity=4)    150
    #                       |
    #                       |
    #                       |
    #                       |
    #             70    (severity 7)    100
    #                     LEVEL 2
    #             60    (severity x)     80
    #                       |
    #                       |
    def test_09_verify_db_info(self):
        logging.info('%%% test_09_verify_db_info %%%')

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging,
                             builddir, 0,
                             redis_password='contrail'))
        assert vizd_obj.verify_on_setup()
        assert vizd_obj.set_opserver_db_info(vizd_obj.opserver,
                                             50, 90, 50, 90)
        assert vizd_obj.verify_collector_db_info(vizd_obj.collectors[0],
                                                 50, 90,
                                                 2147483647, 2147483647)

        logging.info('%%% test_09_verify_db_info - test#1 %%%')
        assert vizd_obj.set_opserver_db_info(vizd_obj.opserver,
                                             40, 50, 40, 50)
        assert vizd_obj.verify_collector_db_info(vizd_obj.collectors[0],
                                                 40, 50,
                                                 2147483647, 2147483647)

        logging.info('%%% test_09_verify_db_info - test#2 %%%')
        assert vizd_obj.set_opserver_db_info(vizd_obj.opserver,
                                             65, 120, 65, 120)
        assert vizd_obj.verify_collector_db_info(vizd_obj.collectors[0],
                                                 65, 120,
                                                 2147483647, 7)

        logging.info('%%% test_09_verify_db_info - test#3 %%%')
        assert vizd_obj.set_opserver_db_info(vizd_obj.opserver,
                                             72, 120, 72, 120)
        assert vizd_obj.verify_collector_db_info(vizd_obj.collectors[0],
                                                 72, 120,
                                                 7, 7)

        logging.info('%%% test_09_verify_db_info - test#4 %%%')
        assert vizd_obj.set_opserver_db_info(vizd_obj.opserver,
                                             87, 85, 87, 85)
        assert vizd_obj.verify_collector_db_info(vizd_obj.collectors[0],
                                                 87, 85,
                                                 3, 7)

        logging.info('%%% test_09_verify_db_info - test#5 %%%')
        assert vizd_obj.set_opserver_db_info(vizd_obj.opserver,
                                             45, 65, 45, 65)
        assert vizd_obj.verify_collector_db_info(vizd_obj.collectors[0],
                                                 45, 65,
                                                 2147483647, 2147483647)

        logging.info('%%% test_09_verify_db_info - test#6 %%%')
        assert vizd_obj.set_opserver_db_info(vizd_obj.opserver,
                                             pending_compaction_tasks_in = 250,
                                             disk_usage_percentage_out = 45,
                                             pending_compaction_tasks_out = 250)
        assert vizd_obj.verify_collector_db_info(vizd_obj.collectors[0],
                                                 45, 250,
                                                 2147483647, 3)

        return True
    # end test_09_verify_db_info

    #@unittest.skip('Skipping AnalyticsApiInfo UVE test')
    def test_10_analytics_api_info_uve(self):

        '''
        This test starts redis, vizd, opserver, qed, and a python generator
        that simulates analytics API

        Reads rest_api_ip and host_ip of OpServer as AnalyticsApiInfoUVE
        Test case doesn't invoke AnalyticsAPiInfo UVE add
        and UVE delete.

        '''
        logging.info("%%% test_10_analytics_api_info_uve %%%")

        vizd_obj = self.useFixture(
                AnalyticsFixture(logging, builddir, 0))
        table = _OBJECT_TABLES[COLLECTOR_INFO_TABLE].log_query_name
        assert vizd_obj.verify_on_setup()
        assert vizd_obj.verify_analytics_api_info_uve(
                    hostname = socket.getfqdn("127.0.0.1"),
                    analytics_table = table,
                    rest_api_ip = '0.0.0.0',
                    host_ip = "127.0.0.1")
        return True

    #@unittest.skip('Skipping test_11_analytics_generator_timeout')
    def test_11_analytics_generator_timeout(self):

        '''
        This test starts redis, vizd, opserver, qed, and a python generator
        that simulates simulates vrouter.
        1. check vrouter generator in collector
        2. check generator successful connection
        2. delete vrouter generator in redis NGENERATORS
        3. send uve
        4. check generator successful connection again
        '''
        logging.info('%%% test_11_analytics_generator_timeout %%%')

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0))
        assert vizd_obj.verify_on_setup()
        collectors = [vizd_obj.get_collector()]
        generator_obj = self.useFixture(
            GeneratorFixture("contrail-vrouter-agent", collectors,
                             logging, vizd_obj.get_opserver_port()))
        assert generator_obj.verify_on_setup()

        source = socket.getfqdn("127.0.0.1")
        exp_genlist = [
            source+':Analytics:contrail-collector:0',
            source+':Analytics:contrail-analytics-api:0',
            source+':Database:contrail-query-engine:0',
            source+':Test:contrail-vrouter-agent:0',
        ]
        assert vizd_obj.verify_generator_list(vizd_obj.collectors,
                                              exp_genlist)

        assert vizd_obj.verify_generator_connected_times(
                                            source+':Test:contrail-vrouter-agent:0', 1)
        assert vizd_obj.delete_generator_from_ngenerator(vizd_obj.collectors[0].get_redis_uve(),
                                                  source+':Test:contrail-vrouter-agent:0')
        generator_obj.send_vm_uve(vm_id='abcd',
                                  num_vm_ifs=1,
                                  msg_count=1)
        time.sleep(1)
        assert vizd_obj.verify_generator_connected_times(
                                            source+':Test:contrail-vrouter-agent:0', 2)
    # end test_11_verify_generator_timeout

    #@unittest.skip('Skipping UVE/Alarm get test')
    def test_12_uve_get_alarm(self):
        '''
        This test case will start Compute node with alarm configed
        and at same time trigger vn_table to send alarms. That is,
        we have two tabls with two different alarms, one table own
        one.  With this way to test if:
            1. get_alarms api return all alarms
            2. get_alarms api return correct alarms in correct table
        '''
        logging.info('%%% test_12_uve_get_alarm %%%')

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0, start_kafka = True))
        assert vizd_obj.verify_on_setup()
        collector = vizd_obj.collectors[0].get_addr()

        api_server_name = socket.getfqdn("127.0.0.1")+'_1'
        api_server = self.useFixture(
            GeneratorFixture('contrail-api', [collector], logging,
                             None, node_type='Config',
                             hostname=api_server_name))
        vr_agent_name = socket.getfqdn("127.0.0.1")+'_1'
        vr_agent = self.useFixture(
            GeneratorFixture('contrail-vrouter-agent', [collector],
                             logging, None, node_type='Compute',
                             hostname=vr_agent_name))
        alarm_gen1_name = socket.getfqdn("127.0.0.1")+'_1'
        alarm_gen1 = self.useFixture(
            GeneratorFixture('contrail-alarm-gen', [collector], logging,
                             None, node_type='Analytics',
                             hostname=alarm_gen1_name))
        api_server.verify_on_setup()
        vr_agent.verify_on_setup()
        alarm_gen1.verify_on_setup()

        vn_list = ['default-domain:project1:vn1',
                   'default-domain:project1:vn2']
        # generate UVEs for the filter test
        api_server.send_vn_config_uve(name=vn_list[0],
                                      partial_conn_nw=[vn_list[1]],
                                      num_acl_rules=2)
        api_server.send_vn_config_uve(name=vn_list[1],
                                      num_acl_rules=3)
        vr_agent.send_vn_agent_uve(name=vn_list[1], num_acl_rules=3,
                                   ipkts=2, ibytes=1024)
        # generate Alarms for the filter test
        alarms = alarm_gen1.create_alarm('InPktsThreshold')
        alarms += alarm_gen1.create_alarm('InBytesThreshold', ack=True)
        alarm_gen1.send_alarm(vn_list[1], alarms, VN_TABLE)
        expected_uves = {
                'analytics-node': [
                {  'name' : socket.getfqdn("127.0.0.1"),
                   'value': {  'UVEAlarms': {
                        'alarms': [
                             {  'severity': 1,
                                'alarm_rules': {
                                     'or_list': [
                                        {  'and_list': [
                                             {  'condition': {
                                                   'operation': '==',
                                                   'operand1' : 'NodeStatus.process_info',
                                                   'variables': [],
                                                   'operand2': {
                                                       'json_value': 'null'
                                                   }
                                                 },
                                                 'match': [
                                                     {  'json_operand1_value': 'null',
                                                        'json_variables': {}
                                                     }
                                                  ]
                                             }
                                          ]
                                         }
                                     ]
                                },
                                'ack': False,
                                'type': 'default-global-system-config:process-status',
                                'description': 'Process Failure. NodeMgr reports abnormal status for process(es) in NodeStatus.process_info'
                             }
                        ]}}}
                ],
                'virtual-network': [
                 {  'name' : 'default-domain:project1:vn2',
                    'value' : { 'UVEAlarms': {
                        'alarms': [
                            {
                                'type': 'InPktsThreshold',
                            },
                            {
                                'type': 'InBytesThreshold',
                                'ack': True
                            }
                        ]
                    } }
                 },
                ]
        }
        assert(vizd_obj.verify_get_alarms(None, exp_uves = expected_uves,
	    contains_=True))
    # end test_12_uve_get_alarm

    #@unittest.skip('Skipping analytics_ssl_params ssl_enable set as true')
    def test_16_analytics_ssl_params_ssl_enable_true(self):

        '''
        This test starts redis, vizd, opserver, qed, and a python generator
        that simulates vrouter and sends UveVirtualMachineAgentTrace messages.
        Then it checks that the VM UVE (via redis) can be accessed from
        opserver using HTTPS.
        '''
        logging.info("%%% test_16_analytics_ssl_params_ssl_enable_true %%%")

        server_ssl_params = {
            'ssl_enable': True,
            'insecure_enable' : False,
            'keyfile': builddir + '/opserver/test/data/ssl/server-privkey.pem',
            'certfile': builddir + '/opserver/test/data/ssl/server.pem',
            'ca_cert': builddir + '/opserver/test/data/ssl/ca-cert.pem',
        }
        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
                             analytics_server_ssl_params=server_ssl_params))
        assert vizd_obj.verify_on_setup()
        collectors = [vizd_obj.get_collector()]

        sandesh_cfg = {
            'sandesh_keyfile': builddir+'/opserver/test/data/ssl/server-privkey.pem',
            'sandesh_certfile': builddir+'/opserver/test/data/ssl/server.pem',
            'sandesh_ca_cert': builddir+'/opserver/test/data/ssl/ca-cert.pem',
            'introspect_ssl_enable': 'True'
        }

        generator_obj = self.useFixture(
            GeneratorFixture("contrail-vrouter-agent", collectors,
                             logging, vizd_obj.get_opserver_port()))
        assert generator_obj.verify_on_setup()
        generator_obj.set_sandesh_config(sandesh_cfg)
        generator_obj.send_vm_uve(vm_id='abcd',
                                  num_vm_ifs=5,
                                  msg_count=5)
        assert generator_obj.verify_vm_uve(vm_id='abcd',
                                           num_vm_ifs=5,
                                           msg_count=5)
        #end test_16_analytics_ssl_params_ssl_enable_true


    #@unittest.skip('Skipping analytics_ssl_params_ssl_enable set as false')
    def test_17_analytics_ssl_params_ssl_enable_false(self):

        '''
        This test starts redis, vizd, opserver, qed, and a python generator
        that simulates vrouter and sends UveVirtualMachineAgentTrace messages.
        Then it checks that the VM UVE (via redis) can be accessed from
        opserver using HTTP.
        '''
        logging.info("%%% test_17_analytics_ssl_params_ssl_enable_false %%%")

        server_ssl_params = {
            'ssl_enable': False,
            'insecure_enable' : False,
            'keyfile': builddir + '/opserver/test/data/ssl/server-privkey.pem',
            'certfile': builddir + '/opserver/test/data/ssl/server.pem',
            'ca_cert': builddir + '/opserver/test/data/ssl/ca-cert.pem',
        }
        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
                             analytics_server_ssl_params=server_ssl_params))
        assert vizd_obj.verify_on_setup()
        collectors = [vizd_obj.get_collector()]

        generator_obj = self.useFixture(
            GeneratorFixture("contrail-vrouter-agent", collectors,
                             logging, vizd_obj.get_opserver_port()))
        assert generator_obj.verify_on_setup()
        generator_obj.send_vm_uve(vm_id='abcd',
                                  num_vm_ifs=5,
                                  msg_count=5)
        assert generator_obj.verify_vm_uve(vm_id='abcd',
                                           num_vm_ifs=5,
                                           msg_count=5)
        #end test_17_analytics_ssl_params_ssl_enable_false


    #@unittest.skip('Skipping analytics_ssl_params_wrong_cacert test')
    def test_18_analytics_ssl_params_wrong_cacert(self):

        '''
        This test starts redis, vizd, opserver, qed, and a python generator
        that simulates vrouter and sends UveVirtualMachineAgentTrace messages.
        Then it checks that the VM UVE (via redis) can be accessed from
        opserver using HTTPS,but with wrong ca-cert.
        Client should not be able to access.
        '''
        logging.info("%%% test_18_analytics_ssl_params_wrong_cacert %%%")

        server_ssl_params = {
                'ssl_enable': True,
                'insecure_enable' : False,
                'keyfile': builddir + '/opserver/test/data/ssl/server-privkey.pem',
                'certfile': builddir + '/opserver/test/data/ssl/server.pem',
                'ca_cert': builddir + '/opserver/test/data/ssl/ca-cert.pem',
        }
        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
                             analytics_server_ssl_params=server_ssl_params))
        assert vizd_obj.verify_on_setup()
        collectors = [vizd_obj.get_collector()]

        sandesh_cfg = {
            'sandesh_keyfile': builddir+'/opserver/test/data/ssl/server-privkey.pem',
            'sandesh_certfile': builddir+'/opserver/test/data/ssl/server.pem',
            'sandesh_ca_cert': builddir+'/opserver/test/data/ssl/server.pem',
            'introspect_ssl_enable': 'True'
        }
        generator_obj = self.useFixture(
            GeneratorFixture("contrail-vrouter-agent", collectors,
                             logging, vizd_obj.get_opserver_port()))
        assert generator_obj.verify_on_setup()
        generator_obj.set_sandesh_config(sandesh_cfg)
        generator_obj.send_vm_uve(vm_id='abcd',
                                  num_vm_ifs=5,
                                  msg_count=5)
        assert not generator_obj.verify_vm_uve(vm_id='abcd',
                                               num_vm_ifs=5,
                                               msg_count=5)
        #end test_18_analytics_ssl_params_wrong_cacert

    #@unittest.skip('Skipping analytics_ssl_params_client_ssl_not_enabled test')
    def test_19_analytics_ssl_params_client_ssl_not_enabled(self):

        '''
        This test starts redis, vizd, opserver, qed, and a python generator
        that simulates vrouter and sends UveVirtualMachineAgentTrace messages.
        Then it checks that the VM UVE (via redis) can be accessed from
        opserver using HTTP.
        Client should not be able to access.
        '''
        logging.info("%%% test_19_analytics_ssl_params_client_ssl_not_enabled %%%")

        server_ssl_params = {
                'ssl_enable': True,
                'insecure_enable' : False,
                'keyfile': builddir + '/opserver/test/data/ssl/server-privkey.pem',
                'certfile': builddir + '/opserver/test/data/ssl/server.pem',
                'ca_cert': builddir + '/opserver/test/data/ssl/ca-cert.pem',
        }
        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
                             analytics_server_ssl_params=server_ssl_params))
        assert vizd_obj.verify_on_setup()
        collectors = [vizd_obj.get_collector()]

        generator_obj = self.useFixture(
            GeneratorFixture("contrail-vrouter-agent", collectors,
                             logging, vizd_obj.get_opserver_port()))
        assert generator_obj.verify_on_setup()
        # client tries to access analytics api with http, it should fail
        generator_obj.send_vm_uve(vm_id='abcd',
                                  num_vm_ifs=5,
                                  msg_count=5)
        assert not generator_obj.verify_vm_uve(vm_id='abcd',
                                               num_vm_ifs=5,
                                               msg_count=5)
        #end test_19_analytics_ssl_params_client_ssl_not_enabled


    #@unittest.skip('Skipping redis HA test')
    def test_20_redis_ha(self):
        '''
        This test starts two redis,two vizd, opserver, qed, and a python generator
        that simulates vrouter and sends UveVirtualMachineAgentTrace messages.
        Then it checks that the VM UVE (via redis) can be accessed from
        opserver after stopping any one of the two running redis.
        '''
        logging.info('%%% test_20_redis_ha %%%')

        vizd_obj = self.useFixture(
            AnalyticsFixture(logging, builddir, 0,
                             collector_ha_test=True))
        assert vizd_obj.verify_on_setup()
        collectors = [vizd_obj.collectors[1].get_addr(),
                      vizd_obj.collectors[0].get_addr()]
        generator_obj = self.useFixture(
            GeneratorFixture("contrail-vrouter-agent", collectors,
                             logging, vizd_obj.get_opserver_port()))
        assert generator_obj.verify_on_setup()
        #Sending the UVEs from generator
        generator_obj.send_vm_uve(vm_id='abcd',
                                  num_vm_ifs=5,
                                  msg_count=5)
        assert generator_obj.verify_vm_uve(vm_id='abcd',
                                           num_vm_ifs=5,
                                           msg_count=5)

        # stopping redis-uve and verifying vm_uve
        vizd_obj.redis_uves[0].stop()
        time.sleep(1)
        assert generator_obj.verify_vm_uve(vm_id='abcd',
                                           num_vm_ifs=5,
                                           msg_count=5)

        vizd_obj.redis_uves[0].start()

        #Stopping other redis and verifying the vm uve
        vizd_obj.redis_uves[1].stop()
        time.sleep(1)
        assert generator_obj.verify_vm_uve(vm_id='abcd',
                                           num_vm_ifs=5,
                                           msg_count=5)

        vizd_obj.redis_uves[1].start()

        #Stopping both redis and verifying. It should fail.
        vizd_obj.redis_uves[0].stop()
        vizd_obj.redis_uves[1].stop()
        assert not generator_obj.verify_vm_uve(vm_id='abcd',
                                           num_vm_ifs=5,
                                           msg_count=5)

        #end test_20_redis_ha

    def test_21_analytics_tls_version_negotiation(self):
        '''
        Since we have disabled tls version negotaition, we will verify below two
        scenarios after starting opserver.
        1. Send a curl request with supported version(tlsv1.2) and check
        that analytics introspect page is accesible using HTTPS.
        2. Send a curl request with unsupported versions(<tlsv1.2) and check
        that analytics introspect page should not be accesible using HTTPS.
        '''
        logging.info("%%% test_21_analytics_tls_version_negotiation %%%")

        server_ssl_params = {
            'ssl_enable': True,
            'insecure_enable' : False,
            'keyfile': builddir + '/opserver/test/data/ssl/server-privkey.pem',
            'certfile': builddir + '/opserver/test/data/ssl/server.pem',
            'ca_cert': builddir + '/opserver/test/data/ssl/ca-cert.pem',
        }
        analytics_obj = AnalyticsFixture(logging, builddir, 0,
		analytics_server_ssl_params=server_ssl_params)
        vizd_obj = self.useFixture(analytics_obj)
        assert vizd_obj.verify_on_setup()
        # supported version(tlsv1.2)
        assert analytics_obj.verify_analytics_tls_version_negotiation('--tlsv1.2',
                server_ssl_params)
        # unsupported version(<tlsv1.2)
        assert not analytics_obj.verify_analytics_tls_version_negotiation('--tlsv1.1',
                server_ssl_params)
        assert not analytics_obj.verify_analytics_tls_version_negotiation('--tlsv1.0',
                server_ssl_params)
        assert not analytics_obj.verify_analytics_tls_version_negotiation('--sslv3',
                server_ssl_params)
        assert not analytics_obj.verify_analytics_tls_version_negotiation('--sslv2',
                server_ssl_params)
    #end test_21_analytics_tls_version_negotiation

    def test_22_zookeeper_node(self):

        '''
        This test case is to check if one node become unreachable then zookeeper
        delete the node after timeout. This test case is to check that should
        n't happen.
        So, first it will add iptable rule to block all traffic to zookeeper
        port then check for zk nodes and then remove iptable rule and then check
        for zk nodes.
        '''
        logging.info("%%% test_22_zookeeper_node %%%")

        vizd_obj = self.useFixture(
                AnalyticsFixture(logging, builddir, 0))
        assert vizd_obj.verify_on_setup()

        assert vizd_obj.check_zk_node()
        add_iptables_rule(vizd_obj.get_zk_port())
        time.sleep(40)
        assert not vizd_obj.check_zk_node()
        delete_iptables_rule(vizd_obj.get_zk_port())
        time.sleep(15)
        assert vizd_obj.check_zk_node()

    @staticmethod
    def get_free_port():
        cs = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cs.bind(("", 0))
        cport = cs.getsockname()[1]
        cs.close()
        return cport


def _term_handler(*_):
    raise IntSignal()


if __name__ == '__main__':
    gevent_signal(signal.SIGINT,_term_handler)
    unittest.main(catchbreak=True)
